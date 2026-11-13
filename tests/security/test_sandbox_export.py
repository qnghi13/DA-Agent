"""Result export in a real sandbox container (docs/design/2026-11-14-result-export.md). Attacks skip
the SQL guard on purpose, so the engine and the container must hold on their own.

Needs Docker. Skipped when Docker is not available.
"""

import csv

import duckdb
import pytest

from da_agent.runner.runner import ExportRun, Runner, RunnerConfig
from tests.security.test_sandbox_escape import IMAGE, _docker_ready, build_image

pytestmark = [pytest.mark.integration, pytest.mark.security]
CANARY = "EXPORT-SANDBOX-CANARY-7731"


@pytest.fixture(scope="module")
def runner(tmp_path_factory: pytest.TempPathFactory) -> Runner:
    if not _docker_ready():
        pytest.skip("Docker is not available")
    build_image()
    base = tmp_path_factory.mktemp("export")
    base.chmod(0o755)
    root, staging = base / "datasets", base / "staging"
    root.mkdir(mode=0o755)
    staging.mkdir(mode=0o755)
    for name, note in (("shop_1", "Hà Nội"), ("shop_2", CANARY)):
        directory = root / name
        directory.mkdir(mode=0o755)
        database = directory / "dataset.duckdb"
        with duckdb.connect(str(database)) as connection:
            connection.execute(
                "CREATE TABLE sales AS SELECT range AS id, range * 10 AS amount, "
                "? AS note FROM range(2000)",
                [note],
            )
        database.chmod(0o644)
    (root / "answers.jsonl").write_text(CANARY, encoding="utf-8")
    config = RunnerConfig(image=IMAGE, datasets_root=root, staging_root=staging, memory_mb=256)
    return Runner(config)


def export(runner: Runner, sql: str):
    return runner.export(ExportRun(dataset_id="shop_1", sql=sql))


def test_an_export_writes_every_row(runner: Runner) -> None:
    result = export(runner, "SELECT id, amount, note FROM sales ORDER BY id")

    assert result.status == "ok", result.stderr
    assert result.report is not None and result.report.error is None
    assert result.report.rows == 2000
    assert result.output_dir is not None
    with (result.output_dir / "result.csv").open(encoding="utf-8-sig", newline="") as file:
        rows = list(csv.reader(file))
    assert rows[0] == ["id", "amount", "note"]
    assert rows[1] == ["0", "0", "Hà Nội"]
    assert len(rows) == 2001


def test_a_query_error_comes_back_in_the_report(runner: Runner) -> None:
    result = export(runner, "SELECT * FROM missing")

    assert result.status == "ok", result.stderr
    assert result.report is not None and result.report.error
    assert result.output_dir is not None
    assert not (result.output_dir / "result.csv").exists()


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM read_csv('/data/../shop_2/dataset.duckdb')",
        "SELECT content FROM read_text('/proc/self/environ')",
        "SELECT * FROM glob('/data/../*')",
        "ATTACH '/data/../shop_2/dataset.duckdb' AS other",
        "COPY sales TO '/out/stolen.csv'",
        "SET enable_external_access = true",
    ],
)
def test_an_export_sees_only_its_dataset(runner: Runner, sql: str) -> None:
    result = export(runner, sql)

    assert CANARY not in result.model_dump_json()
    if result.output_dir is not None:
        assert sorted(p.name for p in result.output_dir.iterdir()) == ["export_report.json"]
        assert result.report is not None and result.report.error
