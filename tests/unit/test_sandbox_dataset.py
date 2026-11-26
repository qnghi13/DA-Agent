"""SandboxDataset: the agent's Dataset over a clean DuckDB file, queried through the Runner."""

import datetime as dt

import pytest

from da_agent.ingestion.build import BuildReport, ColumnBuild, TableBuild
from da_agent.runner.runner import DateSpan, SqlResult, SqlRun, StatsResult, StatsRun
from da_agent.storage.sandbox_dataset import SandboxDataset
from da_agent.storage.sqlite_dataset import QueryError

REPORT = BuildReport(
    tables=[
        TableBuild(
            name="orders",
            source="don",
            description="one order line",
            rows=120,
            columns=[
                ColumnBuild(name="order_id", source="Mã", type="text"),
                ColumnBuild(
                    name="status", source="status", type="text", values=["done", "cancelled"]
                ),
                ColumnBuild(name="total", source="Tổng", type="money", non_blank=100, parsed=97),
                ColumnBuild(
                    name="day",
                    source="Ngày",
                    type="date",
                    min_value="2025-01-01",
                    max_value="2026-06-28",
                ),
            ],
        ),
        TableBuild(name="broken", source="x", error="build failed"),
    ]
)


class FakeRunner:
    def __init__(self, result: SqlResult) -> None:
        self.result = result
        self.requests: list[SqlRun] = []

    def query(self, request: SqlRun) -> SqlResult:
        self.requests.append(request)
        return self.result


def test_describe_lists_built_tables_with_their_column_types() -> None:
    dataset = SandboxDataset(FakeRunner(SqlResult(status="ok")), "shop_1", REPORT)

    [table] = dataset.describe()

    assert (table.name, table.row_count) == ("orders", 120)
    assert [(c.name, c.type) for c in table.columns] == [
        ("order_id", "VARCHAR"),
        ("status", "VARCHAR"),
        ("total", "BIGINT"),
        ("day", "DATE"),
    ]


def test_a_query_runs_in_the_sandbox_and_returns_its_rows() -> None:
    runner = FakeRunner(SqlResult(status="ok", columns=["n"], rows=[[3]], truncated=False))
    dataset = SandboxDataset(runner, "shop_1", REPORT, max_rows=20)

    result = dataset.query("SELECT count(*) AS n FROM orders;")

    assert (result.columns, result.rows, result.truncated) == (["n"], [[3]], False)
    assert runner.requests == [SqlRun(dataset_id="shop_1", sql="SELECT count(*) AS n FROM orders",
                                      max_rows=20)]  # fmt: skip


def test_the_guard_rejects_a_query_before_any_sandbox_starts() -> None:
    runner = FakeRunner(SqlResult(status="ok"))
    dataset = SandboxDataset(runner, "shop_1", REPORT)

    with pytest.raises(QueryError, match="not allowed"):
        dataset.query("SELECT * FROM read_csv('/etc/passwd')")
    assert runner.requests == []


def test_sandbox_errors_become_query_errors() -> None:
    runner = FakeRunner(SqlResult(status="timeout", error="query stopped: over the 15 s limit"))

    with pytest.raises(QueryError, match="over the 15 s limit"):
        SandboxDataset(runner, "shop_1", REPORT).query("SELECT 1")


def test_describe_keeps_source_names_and_known_values() -> None:
    dataset = SandboxDataset(FakeRunner(SqlResult(status="ok")), "shop_1", REPORT)

    [table] = dataset.describe()
    order_id, status = table.columns[:2]

    assert (order_id.source, order_id.values) == ("Mã", ())
    assert (status.source, status.values) == ("status", ("done", "cancelled"))


def test_describe_keeps_what_one_row_is() -> None:
    dataset = SandboxDataset(FakeRunner(SqlResult(status="ok")), "shop_1", REPORT)

    [table] = dataset.describe()

    assert table.description == "one order line"


def test_describe_keeps_the_period_a_date_column_covers() -> None:
    dataset = SandboxDataset(FakeRunner(SqlResult(status="ok")), "shop_1", REPORT)

    [table] = dataset.describe()
    day = table.columns[-1]

    assert day.value_range == ("2025-01-01", "2026-06-28")
    assert table.columns[0].value_range is None


def test_describe_counts_values_that_could_not_be_read() -> None:
    dataset = SandboxDataset(FakeRunner(SqlResult(status="ok")), "shop_1", REPORT)

    [table] = dataset.describe()

    assert [c.unparsed for c in table.columns] == [0, 0, 3, 0]


def test_query_errors_say_which_layer_stopped_the_query() -> None:
    def kind(status: str, sql: str = "SELECT 1") -> str:
        dataset = SandboxDataset(FakeRunner(SqlResult(status=status, error="x")), "shop_1", REPORT)
        with pytest.raises(QueryError) as caught:
            dataset.query(sql)
        return caught.value.kind

    assert kind("ok", "SELECT * FROM read_csv('/etc/passwd')") == "rejected"
    assert kind("timeout") == "timeout"
    assert kind("killed") == "limit"
    assert kind("error") == "engine"


# Fixed analyses (ADR 0051)

TWO_TABLES = BuildReport(
    tables=[
        *REPORT.tables[:1],
        TableBuild(
            name="calendar",
            source="lich",
            rows=90,
            columns=[
                ColumnBuild(
                    name="cal_day",
                    source="cal_day",
                    type="date",
                    min_value="2026-01-01",
                    max_value="2026-03-31",
                ),
            ],
        ),
    ]
)
SEP = DateSpan(start=dt.date(2026, 9, 1), end=dt.date(2026, 9, 30))
OCT = DateSpan(start=dt.date(2026, 10, 1), end=dt.date(2026, 10, 31))


class FakeStatsRunner(FakeRunner):
    def __init__(self, result: StatsResult) -> None:
        super().__init__(SqlResult(status="ok"))
        self.stats_result = result
        self.stats_requests: list[StatsRun] = []

    def stats(self, request: StatsRun) -> StatsResult:
        self.stats_requests.append(request)
        return self.stats_result


def stats_dataset(result: StatsResult | None = None) -> tuple[SandboxDataset, FakeStatsRunner]:
    runner = FakeStatsRunner(result or StatsResult(status="ok", kind="trend"))
    return SandboxDataset(runner, "shop_1", TWO_TABLES), runner


def test_stats_guards_the_sql() -> None:
    dataset, runner = stats_dataset()

    with pytest.raises(QueryError) as error:
        dataset.stats("trend", "DELETE FROM orders", "d", "t")

    assert error.value.kind == "rejected"
    assert runner.stats_requests == []


def test_stats_sends_the_request_with_its_ranges() -> None:
    dataset, runner = stats_dataset()

    dataset.stats("explain_change", "SELECT day, status, SUM(total) AS t FROM orders GROUP BY ALL",
                  "day", "t", dimensions=["status"], before=SEP, after=OCT)  # fmt: skip

    [request] = runner.stats_requests
    assert (request.analysis, request.dataset_id, request.x, request.y) == (
        "explain_change", "shop_1", "day", "t",
    )  # fmt: skip
    assert request.dimensions == ["status"] and (request.before, request.after) == (SEP, OCT)


def test_coverage_comes_from_the_column_x_is_made_from() -> None:
    dataset, runner = stats_dataset()
    sql = (
        "SELECT CAST(o.day AS DATE) AS d, SUM(o.total) AS t FROM orders o "
        "JOIN calendar c ON c.cal_day = o.day GROUP BY 1"
    )

    dataset.stats("trend", sql, "d", "t")

    request = runner.stats_requests[0]
    assert (request.data_start, request.data_end) == (dt.date(2025, 1, 1), dt.date(2026, 6, 28))


def test_coverage_falls_back_to_the_tables_read() -> None:
    dataset, runner = stats_dataset()
    sql = "SELECT 1 AS d, SUM(total) AS t FROM orders JOIN calendar ON cal_day = day"

    dataset.stats("trend", sql, "d", "t")

    request = runner.stats_requests[0]
    # The overlap of the tables' date columns: a calendar cannot stretch the data's range.
    assert (request.data_start, request.data_end) == (dt.date(2026, 1, 1), dt.date(2026, 3, 31))


def test_an_analysis_error_has_kind_analysis() -> None:
    failed = StatsResult(status="error", error="no rows between a and b", error_kind="analysis")
    dataset, _ = stats_dataset(failed)

    with pytest.raises(QueryError, match="no rows between") as error:
        dataset.stats("trend", "SELECT day AS d, total AS t FROM orders", "d", "t")

    assert error.value.kind == "analysis"


@pytest.mark.parametrize(
    ("status", "kind"), [("timeout", "timeout"), ("killed", "limit"), ("error", "engine")]
)
def test_stats_failures_keep_their_kind(status: str, kind: str) -> None:
    dataset, _ = stats_dataset(StatsResult(status=status, error="stopped"))  # type: ignore[arg-type]

    with pytest.raises(QueryError) as error:
        dataset.stats("trend", "SELECT day AS d, total AS t FROM orders", "d", "t")

    assert error.value.kind == kind
