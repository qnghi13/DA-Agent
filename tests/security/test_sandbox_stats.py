"""Fixed analyses in a real sandbox container (ADR 0051). The SQL here skips the Worker's guard on
purpose, so the engine settings and the container must hold on their own, as for agent queries.

Needs Docker. Skipped when Docker is not available.
"""

import datetime as dt

import duckdb
import pytest

from da_agent.runner.runner import DateSpan, Runner, RunnerConfig, StatsRun
from tests.security.test_sandbox_escape import IMAGE, _docker_ready, build_image

pytestmark = [pytest.mark.integration, pytest.mark.security]

CANARY = "STATS-SANDBOX-CANARY-7310"
SEP = DateSpan(start=dt.date(2026, 9, 1), end=dt.date(2026, 9, 30))
OCT = DateSpan(start=dt.date(2026, 10, 1), end=dt.date(2026, 10, 31))
CHANGE_SQL = "SELECT day, branch, SUM(amount) AS revenue FROM sales GROUP BY ALL"


@pytest.fixture(scope="module")
def runner(tmp_path_factory: pytest.TempPathFactory) -> Runner:
    if not _docker_ready():
        pytest.skip("Docker is not available")
    build_image()
    root = tmp_path_factory.mktemp("datasets")
    root.chmod(0o755)
    for name, secret in (("shop_1", "visible"), ("shop_2", CANARY)):
        directory = root / name
        directory.mkdir(mode=0o755)
        database = directory / "dataset.duckdb"
        with duckdb.connect(str(database)) as connection:
            # 61 days from 1 September: Q1 halves in October, Q3 stays the same.
            connection.execute(
                "CREATE TABLE sales AS SELECT DATE '2026-09-01' + (i // 2)::INT AS day, "
                "CASE WHEN i % 2 = 0 THEN 'Q1' ELSE 'Q3' END AS branch, "
                "CASE WHEN i % 2 = 0 AND i >= 60 THEN 50 ELSE 100 END AS amount, "
                "? AS note FROM range(122) r(i)",
                [secret],
            )
            connection.execute(
                "CREATE TABLE big AS SELECT DATE '2026-01-01' + (i % 300)::INT AS day, "
                "'B' || (i % 7)::VARCHAR AS branch, (i % 97)::DOUBLE AS amount "
                "FROM range(1000000) r(i)"
            )
        database.chmod(0o644)
    (root / "answers.jsonl").write_text(CANARY, encoding="utf-8")
    return Runner(RunnerConfig(image=IMAGE, datasets_root=root, memory_mb=512))


def change(runner: Runner, sql: str = CHANGE_SQL, **fields):
    request = {
        "dataset_id": "shop_1",
        "analysis": "explain_change",
        "sql": sql,
        "x": "day",
        "y": "revenue",
        "dimensions": ["branch"],
        "before": SEP,
        "after": OCT,
        "data_start": SEP.start,
        "data_end": OCT.end,
    }
    return runner.stats(StatsRun(**(request | fields)))


def test_explain_change_runs_on_its_own_dataset(runner: Runner) -> None:
    result = change(runner)

    assert result.status == "ok", result.error
    summary = result.summary
    assert summary["top"]["segment"] == "Q1"
    rows = [row for row in (result.table or {})["rows"] if row[0] == "branch"]
    assert sum(row[4] for row in rows) == pytest.approx(summary["change"])
    # Q1: 30 x 100 before, 31 x 50 after; Q3: 30 x 100 before, 31 x 100 after.
    assert summary["change"] == pytest.approx(31 * 150 - 60 * 100)


def test_an_analysis_problem_comes_back_with_its_kind(runner: Runner) -> None:
    result = change(runner, after=DateSpan(start=dt.date(2026, 10, 1), end=dt.date(2026, 11, 5)))

    assert result.status == "error" and result.error_kind == "analysis"
    assert result.error is not None and "same days" in result.error


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM read_csv('/data/../shop_2/dataset.duckdb')",
        "SELECT content AS day, 1 AS branch, 1 AS revenue FROM read_text('/proc/self/environ')",
        "ATTACH '/data/../shop_2/dataset.duckdb' AS other",
        "SELECT * FROM read_text('/data/../answers.jsonl')",
    ],
)
def test_stats_sql_cannot_read_other_datasets_or_files(runner: Runner, sql: str) -> None:
    result = change(runner, sql=sql)

    assert result.status == "error"
    assert CANARY not in (result.error or "")
    assert CANARY not in str(result.summary) + str(result.table)


def test_a_million_rows_fit_in_the_query_memory_limit(runner: Runner) -> None:
    result = change(
        runner,
        sql="SELECT day, branch, amount AS revenue FROM big",
        before=DateSpan(start=dt.date(2026, 1, 1), end=dt.date(2026, 3, 31)),
        after=DateSpan(start=dt.date(2026, 4, 1), end=dt.date(2026, 6, 30)),
        data_start=dt.date(2026, 1, 1),
        data_end=dt.date(2026, 10, 27),
        timeout_seconds=60,
    )

    assert result.status == "ok", result.error
    assert result.duration_seconds < 60
