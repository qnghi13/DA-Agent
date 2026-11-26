"""The analyze tool (ADR 0051): arguments checked by code, additivity read from the SQL, and the
result returned in a shape the model and verification can both read."""

import datetime as dt
import json
from typing import Any

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from da_agent.agent.analysis import (
    ANALYZE,
    CHANGE_GUIDANCE,
    AnalyzeArgumentError,
    additive_problem,
    parse_analyze,
)
from da_agent.agent.tools import execute_tool, tool_specs
from da_agent.llm.types import ToolCall
from da_agent.runner.runner import DateSpan, StatsResult
from da_agent.storage.sqlite_dataset import QueryError, QueryResult, TableInfo

CHANGE_SQL = "SELECT day, branch, SUM(amount) AS revenue FROM orders GROUP BY ALL"
CHANGE = {
    "kind": "explain_change",
    "sql": CHANGE_SQL,
    "x": "day",
    "y": "revenue",
    "dimensions": ["branch"],
    "before": {"start": "2026-09-01", "end": "2026-09-30"},
    "after": {"start": "2026-10-01", "end": "2026-10-31"},
}
RESULT = StatsResult(
    status="ok",
    kind="explain_change",
    summary={"change": -1350.0, "top": {"dimension": "branch", "segment": "Q1"}},
    table={"columns": ["dimension", "segment"], "rows": [["(total)", "(all)"]], "truncated": False},
    notes=[],
    duration_seconds=1.5,
)


class FakeStatsDataset:
    dialect = "DuckDB"

    def __init__(self, result: StatsResult | BaseException = RESULT) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    def describe(self) -> list[TableInfo]:
        return []

    def query(self, sql: str) -> QueryResult:
        return QueryResult(["n"], [[1]], False)

    def stats(self, kind, sql, x, y, *, dimensions=(), before=None, after=None, periods=1):
        self.calls.append({"kind": kind, "sql": sql, "x": x, "y": y, "dimensions": list(dimensions),
                           "before": before, "after": after, "periods": periods})  # fmt: skip
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


def analyze(arguments: dict[str, Any], dataset=None, analyses_left: int | None = 4):
    call = ToolCall(id="c1", name=ANALYZE, arguments=arguments)
    return execute_tool(call, dataset or FakeStatsDataset(), analyses_left=analyses_left)


def test_analyze_is_offered_only_when_asked() -> None:
    plain = [spec.name for spec in tool_specs(None, "DuckDB")]
    asked = [spec.name for spec in tool_specs(None, "DuckDB", analysis=True)]

    assert ANALYZE not in plain
    assert asked == [*plain, ANALYZE]


def test_parse_analyze_reads_ranges_and_dimensions() -> None:
    request = parse_analyze(CHANGE)

    assert request.kind == "explain_change" and request.dimensions == ("branch",)
    assert request.before == DateSpan(start=dt.date(2026, 9, 1), end=dt.date(2026, 9, 30))
    assert request.after is not None and request.after.end == dt.date(2026, 10, 31)


@pytest.mark.parametrize(
    "change",
    [
        {"kind": "regression"},
        {"sql": 5},
        {"before": None},
        {"before": {"start": "1/9/2026", "end": "2026-09-30"}},
        {"before": {"start": "2026-09-30", "end": "2026-09-01"}},
        {"dimensions": []},
        {"dimensions": ["a", "b", "c", "d", "e"]},
        {"dimensions": "branch"},
    ],
)
def test_bad_explain_change_arguments_are_refused(change: dict[str, Any]) -> None:
    with pytest.raises(AnalyzeArgumentError):
        parse_analyze(CHANGE | change)


@pytest.mark.parametrize("periods", [0, 13, True, "3"])
def test_forecast_periods_are_bounded(periods: Any) -> None:
    arguments = {"kind": "forecast", "sql": "SELECT 1", "x": "m", "y": "v", "periods": periods}
    with pytest.raises(AnalyzeArgumentError, match="periods"):
        parse_analyze(arguments)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT day, SUM(amount) AS revenue FROM orders GROUP BY ALL",
        "SELECT day, COUNT(*) AS revenue FROM orders GROUP BY ALL",
        "SELECT day, quantity * price AS revenue FROM orders GROUP BY ALL",
        "SELECT day, CAST(SUM(amount) AS DOUBLE) AS revenue FROM orders GROUP BY ALL",
        "SELECT day, COALESCE(SUM(amount), 0) AS revenue FROM orders GROUP BY ALL",
        "SELECT day, amount AS revenue FROM orders GROUP BY ALL",
    ],
)
def test_sums_counts_and_plain_columns_are_additive(sql: str) -> None:
    assert additive_problem(sql, "revenue") is None


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT day, AVG(amount) AS revenue FROM orders GROUP BY ALL",
        "SELECT day, COUNT(DISTINCT order_id) AS revenue FROM orders GROUP BY ALL",
        "SELECT day, SUM(amount) / COUNT(*) AS revenue FROM orders GROUP BY ALL",
        "SELECT day, MAX(amount) AS revenue FROM orders GROUP BY ALL",
    ],
)
def test_averages_ratios_and_distinct_counts_are_not(sql: str) -> None:
    problem = additive_problem(sql, "revenue")

    assert problem is not None and "sums and counts" in problem


def test_a_successful_explain_change_returns_its_result_and_guidance() -> None:
    dataset = FakeStatsDataset()

    content, step = analyze(CHANGE, dataset)

    payload = json.loads(content)
    assert payload["kind"] == "explain_change"
    assert payload["arguments"]["before"] == {"start": "2026-09-01", "end": "2026-09-30"}
    assert payload["summary"] == RESULT.summary and payload["table"] == RESULT.table
    assert payload["notes"] == [] and payload["guidance"] == CHANGE_GUIDANCE
    assert (step.tool, step.ok, step.sql, step.row_count) == (ANALYZE, True, CHANGE_SQL, 1)
    assert dataset.calls[0]["dimensions"] == ["branch"]


def test_other_kinds_get_no_guidance() -> None:
    trend = StatsResult(status="ok", kind="trend", summary={"direction": "increasing"})
    arguments = {"kind": "trend", "sql": "SELECT m, v FROM t", "x": "m", "y": "v"}

    content, step = analyze(arguments, FakeStatsDataset(trend))

    assert "guidance" not in json.loads(content) and step.row_count == 1


def test_the_fifth_analysis_is_refused() -> None:
    dataset = FakeStatsDataset()

    _, step = analyze(CHANGE, dataset, analyses_left=0)

    assert (step.ok, step.error_kind) == (False, "analysis")
    assert dataset.calls == []


def test_a_not_additive_y_never_reaches_the_sandbox() -> None:
    dataset = FakeStatsDataset()
    sql = "SELECT day, branch, AVG(amount) AS revenue FROM orders GROUP BY ALL"

    content, step = analyze(CHANGE | {"sql": sql}, dataset)

    assert (step.ok, step.error_kind, step.sql) == (False, "analysis", sql)
    assert "sums and counts" in json.loads(content)["error"]
    assert dataset.calls == []


def test_a_bad_argument_is_an_arguments_error() -> None:
    _, step = analyze(CHANGE | {"dimensions": []})

    assert (step.ok, step.error_kind) == (False, "arguments")


def test_a_query_error_keeps_its_kind() -> None:
    dataset = FakeStatsDataset(QueryError("no rows between 2026-10-01 and 2026-10-31", "analysis"))

    content, step = analyze(CHANGE, dataset)

    assert (step.ok, step.error_kind) == (False, "analysis")
    assert "no rows between" in json.loads(content)["error"]


def test_analyze_unknown_without_the_offer() -> None:
    _, step = analyze(CHANGE, analyses_left=None)

    assert step.error_kind == "unknown_tool"


def test_a_crash_keeps_the_sql() -> None:
    _, step = analyze(CHANGE, FakeStatsDataset(RuntimeError("sandbox lost")))

    assert (step.error_kind, step.sql) == ("crash", CHANGE_SQL)


def test_analyze_records_its_kind_and_time_on_the_span() -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test")

    with tracer.start_as_current_span("tool.analyze"):
        analyze(CHANGE)

    [span] = exporter.get_finished_spans()
    attributes = dict(span.attributes or {})
    assert attributes["da_agent.analysis.kind"] == "explain_change"
    assert attributes["da_agent.analysis.rows"] == 1
    assert attributes["da_agent.analysis.seconds"] == 1.5


@pytest.mark.parametrize(
    "sql",
    [
        "WITH t AS (SELECT day, AVG(amount) AS revenue FROM orders GROUP BY day) "
        "SELECT day, revenue FROM t",
        "SELECT day, revenue FROM (SELECT day, SUM(a) / COUNT(*) AS revenue FROM o GROUP BY day) s",
        "WITH t AS (SELECT day, AVG(amount) AS aov FROM orders GROUP BY day) "
        "SELECT day, aov AS revenue FROM t",
    ],
)
def test_an_average_inside_a_cte_or_subquery_is_not_additive(sql: str) -> None:
    problem = additive_problem(sql, "revenue")

    assert problem is not None and "sums and counts" in problem


def test_a_sum_inside_a_cte_is_additive() -> None:
    sql = (
        "WITH t AS (SELECT day, branch, SUM(amount) AS revenue FROM orders GROUP BY ALL) "
        "SELECT day, branch, revenue FROM t"
    )
    assert additive_problem(sql, "revenue") is None
