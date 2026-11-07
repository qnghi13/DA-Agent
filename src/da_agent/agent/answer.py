"""The agent's structured output. Eval adapters convert it, and the core knows no contest format."""

import json
import math
import re
from dataclasses import dataclass, field
from typing import Any

ANSWER_KINDS = ("number", "percent", "date", "boolean", "text", "list", "table", "unanswerable")
DATE_PATTERN = re.compile(r"[0-9]{4}-[0-9]{2}(-[0-9]{2})?")


class InvalidAnswerError(ValueError):
    """The submitted answer is unusable. The message tells the model how to fix it."""


@dataclass(frozen=True)
class AgentAnswer:
    kind: str
    value: Any = None
    reason: str | None = None


@dataclass(frozen=True)
class ToolStep:
    """One tool call in a run. Successful queries are the evidence behind an answer."""

    tool: str
    ok: bool
    sql: str | None = None
    row_count: int | None = None
    error: str | None = None
    # The Vega-Lite spec a successful make_chart built from its query.
    chart: dict[str, Any] | None = None


@dataclass(frozen=True)
class QueryRecord:
    """A successful query and the bounded result the model saw, for reports and provenance."""

    sql: str
    columns: list[str]
    rows: list[list[Any]]
    truncated: bool = False


@dataclass(frozen=True)
class AgentResult:
    answer: AgentAnswer | None = None
    stop_reason: str = "answered"
    steps: list[ToolStep] = field(default_factory=list)
    iterations: int = 0
    error: str | None = None
    # Set when an interactive run paused to ask the user, with stop_reason "needs_clarification".
    question_for_user: str | None = None
    # What each successful run_sql returned, in order. Numbers in the answer trace to these.
    queries: list[QueryRecord] = field(default_factory=list)

    @property
    def charts(self) -> list[dict[str, Any]]:
        """The Vega-Lite specs drawn during the run, in order."""
        return [step.chart for step in self.steps if step.chart is not None]


def _is_number(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    return isinstance(value, int) or (isinstance(value, float) and math.isfinite(value))


def _is_scalar(value: Any) -> bool:
    return value is None or isinstance(value, bool | str) or _is_number(value)


def _is_table(value: Any) -> bool:
    if not isinstance(value, dict) or set(value) != {"columns", "rows"}:
        return False
    columns, rows = value["columns"], value["rows"]
    if not isinstance(columns, list) or not all(isinstance(column, str) for column in columns):
        return False
    return isinstance(rows, list) and all(
        isinstance(row, list) and len(row) == len(columns) and all(_is_scalar(c) for c in row)
        for row in rows
    )


VALIDATORS = {
    "number": (_is_number, "a JSON number with no units or separators"),
    "percent": (_is_number, "a JSON number of percent points, so 12.4 means 12.4%"),
    "date": (
        lambda v: isinstance(v, str) and DATE_PATTERN.fullmatch(v) is not None,
        'a string such as "2026-08-15" or "2026-08"',
    ),
    "boolean": (lambda v: isinstance(v, bool), "true or false"),
    "text": (lambda v: isinstance(v, str), "a JSON string"),
    "list": (
        lambda v: isinstance(v, list) and all(_is_scalar(item) for item in v),
        "a JSON array of plain values",
    ),
    "table": (_is_table, 'an object such as {"columns": ["a", "b"], "rows": [["x", 1]]}'),
}


def parse_answer(arguments: dict[str, Any]) -> AgentAnswer:
    """Validate submit_answer arguments, or raise InvalidAnswerError."""
    kind = arguments.get("kind")
    if kind not in ANSWER_KINDS:
        raise InvalidAnswerError(f"kind must be one of {list(ANSWER_KINDS)}")
    reason = arguments.get("reason")
    if reason is not None and not isinstance(reason, str):
        raise InvalidAnswerError("reason must be a string")
    if kind == "unanswerable":
        return AgentAnswer(kind="unanswerable", reason=reason)

    raw = arguments.get("value_json")
    if not isinstance(raw, str):
        raise InvalidAnswerError("value_json is required: a string holding the answer as JSON")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise InvalidAnswerError(f"value_json is not valid JSON: {error.msg}") from error

    is_valid, expected = VALIDATORS[kind]
    if not is_valid(value):
        raise InvalidAnswerError(f"for kind {kind!r}, value_json must be {expected}")
    return AgentAnswer(kind=kind, value=value, reason=reason)
