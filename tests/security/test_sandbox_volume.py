"""Sandboxes mounted from a named volume (ADR 0024), as when the Runner runs in a container.

The volume is a local bind volume over a temporary directory, so the test can write the data the
Runner checks and the sandbox reads. Needs Docker 26 or later. Skipped without Docker.
"""

import subprocess
import uuid
from collections.abc import Iterator

import duckdb
import pytest

from da_agent.runner.runner import Runner, RunnerConfig, SqlRun
from tests.security.test_sandbox_escape import IMAGE, _docker_ready, build_image

pytestmark = [pytest.mark.integration, pytest.mark.security]
CANARY = "VOLUME-SANDBOX-CANARY-7731"


@pytest.fixture(scope="module")
def volume_runner(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Runner]:
    if not _docker_ready():
        pytest.skip("Docker is not available")
    build_image()
    data = tmp_path_factory.mktemp("data")
    data.chmod(0o755)
    for name, note in (("shop_1", "visible"), ("shop_2", CANARY)):
        directory = data / "datasets" / name
        directory.mkdir(parents=True, mode=0o755)
        (data / "datasets").chmod(0o755)
        database = directory / "dataset.duckdb"
        with duckdb.connect(str(database)) as connection:
            connection.execute("CREATE TABLE sales AS SELECT 1 AS id, ? AS note", [note])
        database.chmod(0o644)
    volume = f"da-agent-test-{uuid.uuid4().hex[:8]}"
    subprocess.run(
        ["docker", "volume", "create", "--driver", "local", "--opt", "type=none",
         "--opt", "o=bind", "--opt", f"device={data}", volume],
        check=True, capture_output=True,
    )  # fmt: skip
    try:
        yield Runner(
            RunnerConfig(
                image=IMAGE,
                datasets_root=data / "datasets",
                memory_mb=256,
                data_volume=volume,
                data_root=data,
            )
        )
    finally:
        subprocess.run(["docker", "volume", "rm", "-f", volume], check=False, capture_output=True)


def test_a_query_reads_its_dataset_from_the_volume(volume_runner: Runner) -> None:
    result = volume_runner.query(SqlRun(dataset_id="shop_1", sql="SELECT note FROM sales"))

    assert result.status == "ok", result.error
    assert result.rows == [["visible"]]


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM glob('/data/../*')",
        "ATTACH '/data/../shop_2/dataset.duckdb' AS other",
        "SELECT * FROM read_text('/data/../shop_2/dataset.duckdb')",
    ],
)
def test_only_the_dataset_subpath_is_visible(volume_runner: Runner, sql: str) -> None:
    result = volume_runner.query(SqlRun(dataset_id="shop_1", sql=sql))

    assert CANARY not in str(result.rows)
    assert result.status == "error" or "shop_2" not in str(result.rows)
