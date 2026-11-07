"""SandboxDataset: the agent's Dataset over a clean DuckDB file, queried through the Runner."""

import pytest

from da_agent.ingestion.build import BuildReport, ColumnBuild, TableBuild
from da_agent.runner.runner import SqlResult, SqlRun
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
