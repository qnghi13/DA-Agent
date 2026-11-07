"""Code checks on a proposed answer. See docs/adr/0007.

Checks here are cheap and deterministic. A failed check goes back to the tool loop as a repair
request, and an answer that still fails is withheld, because a wrong number costs more than none.

- Evidence: at least one query succeeded.
- Tracing: every number in the answer is, within rounding, a number some query returned.
- Period: the queries behind the answer filter dates to a period the data covers. A sum over a
  month with no rows is 0, which looks like an answer but is not one. Periods are read from the
  SQL with sqlglot, so the check needs no knowledge of the question's language.
"""

import calendar
import datetime as dt
import json
import math
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

import sqlglot
from sqlglot import exp

from da_agent.agent.answer import AgentAnswer
from da_agent.llm.types import Message

RUN_SQL = "run_sql"
DATE_TEXT = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2})(?::(\d{2}))?)?")
# Wrappers that keep a column's date value, such as CAST(d AS DATE) or date(d).
DATE_WRAPPERS = (exp.Cast, exp.TryCast, exp.Date, exp.TsOrDsToDate, exp.TsOrDsToTimestamp)
# Places a condition lives. Bounds in one place narrow one period; separate places are separate
# periods, such as two CASE branches that compare two months.
CONDITION_SCOPES = (exp.If, exp.Where, exp.Having, exp.Join, exp.Filter)
DateRanges = dict[str, list[tuple[str, str]]]


@dataclass(frozen=True)
class Period:
    """A date filter on a column: from start to end, inclusive. None means open."""

    column: str
    start: dt.date | None
    end: dt.date | None


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def _walk_numbers(value: Any) -> list[float]:
    if _is_number(value):
        return [float(value)]
    if isinstance(value, list):
        return [n for item in value for n in _walk_numbers(item)]
    if isinstance(value, dict):
        return [n for item in value.values() for n in _walk_numbers(item)]
    return []


def result_numbers(messages: list[Message]) -> list[float]:
    """Every number in a successful query result the model has seen."""
    numbers: list[float] = []
    for message in messages:
        if message.role != "tool" or message.tool_name != RUN_SQL or not message.content:
            continue
        try:
            payload = json.loads(message.content)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and "rows" in payload:
            numbers += _walk_numbers(payload["rows"])
    return numbers


def answer_numbers(answer: AgentAnswer) -> list[float]:
    if answer.kind in ("number", "percent", "list"):
        return _walk_numbers(answer.value)
    if answer.kind == "table" and isinstance(answer.value, dict):
        return _walk_numbers(answer.value.get("rows", []))
    return []


def _tolerance(value: float) -> float:
    """Half a unit in the last decimal place written, so a rounded result still matches."""
    try:
        exponent = Decimal(repr(value)).normalize().as_tuple().exponent
    except InvalidOperation:
        return 1e-9
    decimals = max(-exponent, 0) if isinstance(exponent, int) else 0
    return 0.5 * 10.0**-decimals


def _traced(value: float, numbers: list[float], percent: bool) -> bool:
    tolerance = _tolerance(value)
    candidates = numbers + ([n * 100 for n in numbers] if percent else [])
    return any(abs(value - n) <= tolerance for n in candidates)


def _text(node: exp.Expression) -> str | None:
    while isinstance(node, exp.Cast | exp.TryCast):
        node = node.this
    if isinstance(node, exp.Literal) and node.is_string:
        return str(node.this)
    return None


def _date(node: exp.Expression) -> tuple[dt.date, bool] | None:
    """A date literal, and whether it is exactly midnight (so `< d` ends the day before)."""
    text = _text(node)
    match = DATE_TEXT.match(text) if text else None
    if match is None:
        return None
    try:
        day = dt.date(int(match[1]), int(match[2]), int(match[3]))
    except ValueError:
        return None
    midnight = not any(part and int(part) for part in match.groups()[3:])
    return day, midnight


def _column(node: exp.Expression) -> str | None:
    while isinstance(node, DATE_WRAPPERS):
        node = node.this
    return node.name.lower() if isinstance(node, exp.Column) else None


def _span(unit: str, first: dt.date) -> tuple[dt.date, dt.date]:
    if unit == "year":
        return dt.date(first.year, 1, 1), dt.date(first.year, 12, 31)
    if unit == "month":
        last = calendar.monthrange(first.year, first.month)[1]
        return first.replace(day=1), first.replace(day=last)
    return first, first


def _formatted(node: exp.Expression, value: str) -> tuple[str, dt.date, dt.date] | None:
    """strftime(col, '%Y-%m') = '2025-03' in either argument order."""
    if not isinstance(node, exp.TimeToStr):
        return None
    parts = [node.this, node.args.get("format")]
    column = next((c for c in map(_column, filter(None, parts)) if c), None)
    fmt = next((t for t in map(_text, filter(None, parts)) if t), None)
    units = {"%Y": ("year", "{}-01-01"), "%Y-%m": ("month", "{}-01"), "%Y-%m-%d": ("day", "{}")}
    if column is None or fmt not in units:
        return None
    unit, template = units[fmt]
    parsed = _date(exp.Literal.string(template.format(value)))
    return (column, *_span(unit, parsed[0])) if parsed else None


def _truncated(node: exp.Expression, day: dt.date) -> tuple[str, dt.date, dt.date] | None:
    """date_trunc('month', col) = DATE '2025-06-01'."""
    if not isinstance(node, exp.TimestampTrunc | exp.DateTrunc):
        return None
    column, unit = _column(node.this), str(node.args.get("unit") or "").lower().strip("'")
    if column is None or unit not in ("month", "year", "day"):
        return None
    return (column, *_span(unit, day))


FLIPPED = {exp.GT: exp.LT, exp.GTE: exp.LTE, exp.LT: exp.GT, exp.LTE: exp.GTE, exp.EQ: exp.EQ}


def _bound(node: exp.Expression) -> tuple[str, dt.date | None, dt.date | None] | None:
    """One date condition as (column, start, end)."""
    if isinstance(node, exp.Between):
        column, low, high = _column(node.this), _date(node.args["low"]), _date(node.args["high"])
        if column and low and high:
            return column, low[0], high[0]
        return None
    left, right, kind = node.this, node.expression, type(node)
    if _column(left) is None and _date(left) is not None:
        left, right, kind = right, left, FLIPPED[kind]
    if kind is exp.EQ:
        value = _text(right)
        found = _formatted(left, value) if value else None
        day = _date(right)
        found = found or (_truncated(left, day[0]) if day else None)
        if found:
            return found
    column, day = _column(left), _date(right)
    if column is None or day is None:
        return None
    value, midnight = day
    if kind is exp.EQ:
        return column, value, value
    if kind in (exp.GT, exp.GTE):
        return column, value, None
    end = value - dt.timedelta(days=1) if kind is exp.LT and midnight else value
    return column, None, end


def query_periods(sql: str, dialect: str) -> list[Period]:
    """The date periods a query filters on, read from its conditions. Empty when none or when
    the SQL does not parse."""
    try:
        tree = sqlglot.parse_one(sql, read=dialect)
    except sqlglot.errors.ParseError:
        return []
    scoped: dict[tuple[int, str], list[dt.date | None]] = {}
    for node in tree.find_all(exp.Between, exp.EQ, exp.GT, exp.GTE, exp.LT, exp.LTE):
        found = _bound(node)
        if found is None:
            continue
        column, start, end = found
        scope = node.find_ancestor(*CONDITION_SCOPES)
        key = (id(scope), column)
        current = scoped.setdefault(key, [None, None])
        if start is not None:
            current[0] = start if current[0] is None else max(current[0], start)
        if end is not None:
            current[1] = end if current[1] is None else min(current[1], end)
    return [Period(column, start, end) for (_, column), (start, end) in scoped.items()]


def date_ranges(columns: list[tuple[str, tuple[str, str] | None]]) -> DateRanges:
    """Known periods by lower-case column name, from (name, (min, max)) pairs."""
    ranges: DateRanges = {}
    for name, value_range in columns:
        if value_range is not None:
            ranges.setdefault(name.lower(), []).append(value_range)
    return ranges


def _overlaps(period: Period, known: list[tuple[str, str]]) -> bool:
    for low, high in known:
        first, last = dt.date.fromisoformat(low[:10]), dt.date.fromisoformat(high[:10])
        if (period.end is None or period.end >= first) and (
            period.start is None or period.start <= last
        ):
            return True
    return False


def _query_results(messages: list[Message]) -> list[tuple[str, list[float], list[Any]]]:
    """Each successful query with the numbers it returned, in order."""
    sql_by_call = {
        call.id: call.arguments.get("sql")
        for message in messages
        if message.role == "assistant"
        for call in message.tool_calls
        if call.name == RUN_SQL
    }
    found = []
    for message in messages:
        sql = sql_by_call.get(message.tool_call_id or "")
        if message.role != "tool" or not isinstance(sql, str) or not message.content:
            continue
        try:
            payload = json.loads(message.content)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and "rows" in payload:
            found.append((sql, _walk_numbers(payload["rows"]), payload["rows"]))
    return found


def _period_problems(
    answer: AgentAnswer, messages: list[Message], ranges: DateRanges, dialect: str
) -> list[str]:
    results = _query_results(messages)
    if not results:
        return []
    values = answer_numbers(answer)
    percent = answer.kind == "percent"
    evidence = [
        sql for sql, numbers, _ in results if any(_traced(v, numbers, percent) for v in values)
    ] if values else [results[-1][0]]  # fmt: skip
    for sql in evidence:
        for period in query_periods(sql, dialect):
            known = ranges.get(period.column)
            if known and not _overlaps(period, known):
                shown = " and ".join(f"{low[:10]} to {high[:10]}" for low, high in known)
                start = period.start.isoformat() if period.start else "the start"
                end = period.end.isoformat() if period.end else "the end"
                return [
                    f"the query behind the answer filters {period.column} to {start} to {end}, "
                    f"but the data only has {period.column} from {shown}, so it has no rows for "
                    "that period and its result is not an answer; if the question asks about a "
                    "period the data does not cover, submit kind 'unanswerable' with the reason"
                ]
    return []


def _show(value: float) -> str:
    return str(int(value)) if value.is_integer() else repr(value)


def check_answer(
    answer: AgentAnswer,
    messages: list[Message],
    queries_ok: int,
    ranges: DateRanges | None = None,
    dialect: str = "duckdb",
) -> list[str]:
    """Problems with a proposed answer, written for the model. Empty when it passes. ranges holds
    the period each date column covers, by lower-case name; without it periods are not checked."""
    if queries_ok == 0:
        return ["no query succeeded, so the answer has no evidence; run the queries it needs"]
    numbers = result_numbers(messages)
    untraced = [
        value
        for value in answer_numbers(answer)
        if not _traced(value, numbers, percent=answer.kind == "percent")
    ]
    if untraced:
        shown = ", ".join(_show(value) for value in untraced[:5])
        return [
            f"the numbers {shown} do not appear in any query result; compute each number in SQL "
            "and answer with the value the query returns"
        ]
    if answer.kind != "unanswerable" and ranges:
        return _period_problems(answer, messages, ranges, dialect)
    return []
