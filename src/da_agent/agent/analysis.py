"""The analyze tool's contract: its spec, its arguments, and the checks the Worker runs before the
sandbox. See docs/adr/0051.

The model names a kind, one SELECT, and columns. Code reads the arguments, refuses what the method
cannot use (an average to split, five dimensions, a range that is not a date), and only then
starts a sandbox. The method itself is code in da_agent.storage.stats and change.
"""

import datetime as dt
from dataclasses import dataclass
from typing import Any, Protocol, get_args

import sqlglot
from pydantic import ValidationError
from sqlglot import exp
from sqlglot.errors import ParseError

from da_agent.llm.types import ToolSpec
from da_agent.runner.runner import DateSpan, StatsKind, StatsResult

ANALYZE = "analyze"
ANALYZE_KINDS: tuple[str, ...] = get_args(StatsKind)
PLAN_ANALYSES = ("none", *ANALYZE_KINDS)
MAX_ANALYSES = 4
MAX_DIMENSIONS = 4
MAX_HORIZON = 12

CHANGE_GUIDANCE = "These are where the change happened, not why it happened; say so in the answer."

_RANGE = {
    "type": "object",
    "properties": {"start": {"type": "string"}, "end": {"type": "string"}},
}

ANALYZE_SPEC = ToolSpec(
    name=ANALYZE,
    description=(
        "Run a fixed analysis on one query's result. Code picks the method and reads every row "
        "the query returns, so aggregate to the level you want first. Kinds: "
        "'explain_change': where did a sum or count change between two periods? x is a date, y "
        "a SUM or COUNT, and dimensions are 1 to 4 columns that could explain the change, such "
        "as branch, channel, or category. before and after are date ranges from the periods "
        "given with the question; group x by day to compare part of a month. "
        "'trend': is y rising or falling? x is one date per period, for example "
        "date_trunc('month', order_date). 'forecast': the next periods of y with a 95% "
        "interval; x as for trend. 'correlation': do two numeric columns x and y move "
        "together? 'outliers': which rows have an unusual y? x labels each row, such as a day. "
        "Unfinished first or last periods are left out for you. Answer with the numbers it "
        "returns."
    ),
    parameters={
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": list(ANALYZE_KINDS)},
            "sql": {
                "type": "string",
                "description": "One SELECT that returns x, y, and any dimensions.",
            },
            "x": {"type": "string", "description": "The column of dates, labels, or values."},
            "y": {"type": "string", "description": "The numeric column to analyse."},
            "dimensions": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": MAX_DIMENSIONS,
                "description": "explain_change only: columns of the query to split by.",
            },
            "before": {
                **_RANGE,
                "description": (
                    "explain_change only: the earlier range, ISO dates, both ends included."
                ),
            },
            "after": {**_RANGE, "description": "explain_change only: the later range."},
            "periods": {
                "type": "integer",
                "description": f"forecast only: how many periods ahead, 1 to {MAX_HORIZON}.",
            },
        },
        "required": ["kind", "sql", "x", "y"],
    },
)

# Added only when analyze is offered, so runs without it keep their prompt.
ANALYSIS_GUIDE = (
    "When to use analyze: to explain where a total changed between two periods "
    "(explain_change), for a trend or growth, a forecast, a correlation, or outliers, call "
    "analyze and answer with its numbers. Do not judge these by reading rows through run_sql: "
    "its results are cut to a few dozen rows. For a single total, count, or ranking, run_sql "
    "is enough."
)

CHANGE_DIRECTIVE = (
    "This question needs an explain_change analysis: call analyze with kind 'explain_change', "
    "a query that returns one row per day and segment with a SUM or COUNT as y, the columns "
    "that could explain the change as dimensions, and before and after ranges from the periods "
    "above. Answer with the numbers it returns, and say that they show where the change "
    "happened, not why. Do not compute it in run_sql. If analyze refuses y because it is not "
    "a sum or a count, such as an average, answer with run_sql instead."
)
KIND_DIRECTIVE = (
    "This question needs a {kind} analysis: call analyze with kind '{kind}' and a query that "
    "returns one row per period or item, then answer with the numbers it returns. Do not "
    "compute it in run_sql."
)


class AnalyzeArgumentError(ValueError):
    """The analyze arguments are unusable. The message tells the model how to fix them."""


@dataclass(frozen=True)
class AnalyzeRequest:
    kind: StatsKind
    sql: str
    x: str
    y: str
    dimensions: tuple[str, ...] = ()
    before: DateSpan | None = None
    after: DateSpan | None = None
    periods: int = 1


class StatsDataset(Protocol):
    """A dataset that can run fixed analyses, such as SandboxDataset."""

    def stats(
        self,
        kind: StatsKind,
        sql: str,
        x: str,
        y: str,
        *,
        dimensions: tuple[str, ...] | list[str] = (),
        before: DateSpan | None = None,
        after: DateSpan | None = None,
        periods: int = 1,
    ) -> StatsResult: ...


def can_stats(dataset: object) -> bool:
    """Whether the dataset runs fixed analyses: SandboxDataset does, SqliteDataset does not."""
    return callable(getattr(dataset, "stats", None))


def _span(value: Any, name: str) -> DateSpan:
    if not isinstance(value, dict):
        raise AnalyzeArgumentError(
            f'{name} must be a range such as {{"start": "2026-09-01", "end": "2026-09-30"}}'
        )
    try:
        start = dt.date.fromisoformat(str(value.get("start")))
        end = dt.date.fromisoformat(str(value.get("end")))
        return DateSpan(start=start, end=end)
    except (ValueError, ValidationError) as error:
        raise AnalyzeArgumentError(
            f"{name} needs ISO dates with start on or before end, such as 2026-09-01"
        ) from error


def parse_analyze(arguments: dict[str, Any]) -> AnalyzeRequest:
    """Read analyze arguments, or raise AnalyzeArgumentError."""
    kind = arguments.get("kind")
    if kind not in ANALYZE_KINDS:
        raise AnalyzeArgumentError(f"kind must be one of {list(ANALYZE_KINDS)}")
    sql, x, y = arguments.get("sql"), arguments.get("x"), arguments.get("y")
    if not all(isinstance(value, str) and value.strip() for value in (sql, x, y)):
        raise AnalyzeArgumentError("sql, x, and y are required strings")
    periods = arguments.get("periods", 1)
    if isinstance(periods, bool) or not isinstance(periods, int) or not 1 <= periods <= MAX_HORIZON:
        raise AnalyzeArgumentError(f"periods must be a whole number from 1 to {MAX_HORIZON}")
    request = AnalyzeRequest(kind=kind, sql=str(sql), x=str(x), y=str(y), periods=periods)
    if kind != "explain_change":
        return request
    dimensions = arguments.get("dimensions")
    if (
        not isinstance(dimensions, list)
        or not 1 <= len(dimensions) <= MAX_DIMENSIONS
        or not all(isinstance(name, str) and name.strip() for name in dimensions)
    ):
        raise AnalyzeArgumentError(
            f"dimensions must list 1 to {MAX_DIMENSIONS} columns of the query to split by"
        )
    return AnalyzeRequest(
        kind=kind,
        sql=str(sql),
        x=str(x),
        y=str(y),
        dimensions=tuple(dimensions),
        before=_span(arguments.get("before"), "before"),
        after=_span(arguments.get("after"), "after"),
        periods=periods,
    )


def request_json(request: AnalyzeRequest) -> dict[str, Any]:
    """The arguments as code read them, with ISO dates. The SQL travels separately."""

    def shown(span: DateSpan | None) -> dict[str, str] | None:
        return (
            None if span is None else {"start": span.start.isoformat(), "end": span.end.isoformat()}
        )

    return {
        "x": request.x,
        "y": request.y,
        "dimensions": list(request.dimensions),
        "before": shown(request.before),
        "after": shown(request.after),
        "periods": request.periods,
    }


_WRAPPERS = (exp.Alias, exp.Cast, exp.TryCast, exp.Coalesce, exp.Paren)


def _unwrapped(value: exp.Expr) -> exp.Expr:
    while isinstance(value, _WRAPPERS) and isinstance(value.this, exp.Expr):
        value = value.this
    return value


def _sources(tree: exp.Expr, select: exp.Select) -> list[tuple[str, exp.Select]]:
    """The CTEs and subqueries a SELECT reads from, by the name the SELECT gives them."""
    ctes = {cte.alias_or_name: cte.this for cte in tree.find_all(exp.CTE)}
    found = []
    from_ = select.args.get("from_")
    for source in [
        from_.this if from_ else None,
        *(j.this for j in select.args.get("joins") or []),
    ]:
        if isinstance(source, exp.Subquery) and isinstance(source.this, exp.Select):
            found.append((source.alias_or_name, source.this))
        elif isinstance(source, exp.Table) and isinstance(ctes.get(source.name), exp.Select):
            found.append((source.alias_or_name, ctes[source.name]))
    return found


def _definition(tree: exp.Expr, select: exp.Select, name: str, depth: int = 0) -> exp.Expr | None:
    """The expression a column of the SELECT stands for, followed into the CTE or subquery it
    comes from, so an average computed inside one is still an average."""
    found = next((p for p in select.expressions if p.alias_or_name == name), None)
    if found is None:
        return None
    value = _unwrapped(found)
    if depth < 5 and isinstance(value, exp.Column):
        sources = _sources(tree, select)
        if value.table:
            sources = [s for s in sources if s[0] == value.table]
        for _, inner in sources:
            deeper = _definition(tree, inner, value.name, depth + 1)
            if deeper is not None:
                return deeper
    return value


def additive_problem(sql: str, y: str, dialect: str = "duckdb") -> str | None:
    """Why y cannot be split into parts that add up to its change, or None when it can: a SUM, a
    COUNT without DISTINCT, or a value with no aggregate that code sums. y is followed into CTEs
    and subqueries. A query that does not parse, or has no column y, is left to the sandbox,
    which names the problem."""
    try:
        tree = sqlglot.parse_one(sql, read=dialect)
    except ParseError:
        return None
    select = tree if isinstance(tree, exp.Select) else tree.find(exp.Select)
    if select is None:
        return None
    value = _definition(tree, select, y)
    if value is None:
        return None
    if not any(True for _ in value.find_all(exp.AggFunc)):
        return None
    distinct = value.find(exp.Distinct) is not None
    if isinstance(value, (exp.Sum, exp.Count)) and not distinct:
        return None
    return (
        f"explain_change splits sums and counts, and y is {value.sql(dialect)} here; use SUM or "
        "COUNT, or answer this one with run_sql"
    )


def analysis_directive(kind: str) -> str:
    """What the tool loop is told when the plan names an analysis; nothing for "none"."""
    if kind == "explain_change":
        return CHANGE_DIRECTIVE
    if kind in ANALYZE_KINDS:
        return KIND_DIRECTIVE.format(kind=kind)
    return ""
