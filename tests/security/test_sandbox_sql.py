"""Agent queries in a real sandbox container (ADR 0011). Attacks skip the SQL guard on purpose, so
the engine and the container must hold on their own.

Needs Docker. Skipped when Docker is not available.
"""

import duckdb
import pytest

from da_agent.runner.runner import Runner, RunnerConfig, SqlRun
from tests.security.test_sandbox_escape import IMAGE, _docker_ready, build_image

pytestmark = [pytest.mark.integration, pytest.mark.security]
CANARY = "SQL-SANDBOX-CANARY-5521"


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
            connection.execute(
                "CREATE TABLE sales AS SELECT range AS id, range * 10 AS amount, "
                "? AS note FROM range(1000)",
                [secret],
            )
        database.chmod(0o644)
    (root / "answers.jsonl").write_text(CANARY, encoding="utf-8")
    return Runner(RunnerConfig(image=IMAGE, datasets_root=root, memory_mb=256))


def query(runner: Runner, sql: str, timeout: int = 15):
    return runner.query(SqlRun(dataset_id="shop_1", sql=sql, timeout_seconds=timeout))


def test_a_query_reads_its_own_dataset(runner: Runner) -> None:
    result = query(runner, "SELECT count(*), sum(amount), min(note) FROM sales")

    assert result.status == "ok", result.error
    assert result.rows == [[1000, 4995000, "visible"]]


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM read_csv('/data/../shop_2/dataset.duckdb')",
        "SELECT content FROM read_text('/proc/self/environ')",
        "SELECT * FROM '/etc/passwd'",
        "SELECT * FROM glob('/data/../*')",
        "ATTACH '/data/../shop_2/dataset.duckdb' AS other",
        "COPY sales TO '/tmp/out.csv'",
        "INSTALL httpfs",
        "SET enable_external_access = true",
        "DELETE FROM sales",
    ],
)
def test_files_other_datasets_and_writes_are_blocked_without_the_guard(
    runner: Runner, sql: str
) -> None:
    result = query(runner, sql)

    assert result.status != "ok" or not result.rows
    assert CANARY not in str(result)
    assert query(runner, "SELECT count(*) FROM sales").rows == [[1000]]


def test_a_runaway_query_times_out(runner: Runner) -> None:
    result = query(
        runner,
        "SELECT sum(hash(a.range * b.range)) FROM range(1000000) a, range(1000000) b",
        timeout=2,
    )

    assert result.status == "timeout"
    assert result.error is not None and "time limit" in result.error
