"""The worker's export job: stored SQL only, checked again, whole files or a clear failure.

Needs DA_AGENT_TEST_POSTGRES_URL. The Runner is a fake; the real sandbox is in tests/security.
"""

import os
import threading
import time
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
import pytest
from psycopg.types.json import Jsonb

from da_agent.runner.client import UNAVAILABLE
from da_agent.runner.runner import ExportResult, ExportRun
from da_agent.storage.csv_export import ExportReport
from da_agent.storage.datasets import DatasetStore
from da_agent.storage.exports import CHANGED_DATASET, export_file
from da_agent.storage.jobs import Job, JobQueue
from da_agent.worker.exports import ExportHandler
from da_agent.worker.worker import PermanentJobError, RetryableJobError

pytestmark = pytest.mark.integration
URL = os.environ.get("DA_AGENT_TEST_POSTGRES_URL", "")
QUERIES = [
    {"sql": "SELECT 1 AS n", "columns": ["n"], "rows": [[1]], "truncated": False},
    {"sql": "SELECT id FROM sales", "columns": ["id"], "rows": [[1]], "truncated": True},
]


class FakeRunner:
    def __init__(self, staging: Path) -> None:
        self.staging = staging
        self.requests: list[ExportRun] = []
        self.result: ExportResult | None = None

    def export(self, request: ExportRun) -> ExportResult:
        self.requests.append(request)
        if self.result is not None:
            return self.result
        out = self.staging / f"da-sbx-{len(self.requests)}"
        out.mkdir(parents=True)
        (out / "result.csv").write_text("id\n1\n2\n", encoding="utf-8")
        report = ExportReport(rows=2, bytes=8, columns=["id"])
        return ExportResult(
            status="ok", exit_code=0, stderr="", report=report, output_dir=out,
            duration_seconds=0.1,
        )  # fmt: skip


def _failed(status: str, stderr: str = "", report: ExportReport | None = None) -> ExportResult:
    return ExportResult(
        status=status, exit_code=None, stderr=stderr, report=report, output_dir=None,
        duration_seconds=0.1,
    )  # fmt: skip


@pytest.fixture
def setup(conn: psycopg.Connection, workspace: UUID, schema: str, tmp_path: Path):
    dataset = DatasetStore(conn).create(workspace, "shop", "")
    conn.execute(
        "UPDATE datasets SET status = 'ready', updated_at = now() - interval '1 hour' "
        "WHERE id = %s",
        (dataset,),
    )
    queue = JobQueue(conn)
    run = queue.enqueue(workspace, "run", {"dataset_id": str(dataset), "question": "q"})
    conn.execute(
        "UPDATE jobs SET status = 'succeeded', result = %s WHERE id = %s",
        (Jsonb({"queries": QUERIES}), run),
    )
    runner = FakeRunner(tmp_path / "staging")
    exports = tmp_path / "exports"
    handler = ExportHandler(url=URL, schema=schema, runner=runner, exports_root=exports)
    return {"dataset": dataset, "run": run, "runner": runner, "exports": exports,
            "handler": handler}  # fmt: skip


def _export_job(
    conn: psycopg.Connection, workspace: UUID, setup: dict[str, Any], index: int = 1
) -> Job:
    payload = {"run_id": str(setup["run"]), "query_index": index,
               "dataset_id": str(setup["dataset"])}  # fmt: skip
    job_id = JobQueue(conn).enqueue(workspace, "export", payload)
    job = JobQueue(conn).get(job_id, workspace)
    assert job is not None
    return job


def test_an_export_runs_the_stored_sql_and_keeps_the_file(
    conn: psycopg.Connection, workspace: UUID, setup: dict[str, Any]
) -> None:
    job = _export_job(conn, workspace, setup)

    result = setup["handler"](job, threading.Event())

    assert setup["runner"].requests == [
        ExportRun(dataset_id=str(setup["dataset"]), sql="SELECT id FROM sales")
    ]
    kept = export_file(setup["exports"], job.id)
    assert kept.read_text(encoding="utf-8") == "id\n1\n2\n"
    assert result == {
        "rows": 2,
        "bytes": kept.stat().st_size,
        "filename": f"da-agent-{str(setup['run'])[:8]}-query-2.csv",
    }
    assert list((setup["runner"].staging).iterdir()) == []


@pytest.mark.security
def test_sql_the_guard_rejects_is_never_run(
    conn: psycopg.Connection, workspace: UUID, setup: dict[str, Any]
) -> None:
    bad = [{**QUERIES[0], "sql": "DROP TABLE sales"}]
    conn.execute(
        "UPDATE jobs SET result = %s WHERE id = %s", (Jsonb({"queries": bad}), setup["run"])
    )
    job = _export_job(conn, workspace, setup, index=0)

    with pytest.raises(PermanentJobError):
        setup["handler"](job, threading.Event())
    assert setup["runner"].requests == []


@pytest.mark.parametrize("index", [2, -1])
def test_an_unknown_query_is_not_found(
    conn: psycopg.Connection, workspace: UUID, setup: dict[str, Any], index: int
) -> None:
    job = _export_job(conn, workspace, setup, index=index)

    with pytest.raises(PermanentJobError, match="not found"):
        setup["handler"](job, threading.Event())


def test_a_dataset_changed_after_the_run_fails_the_export(
    conn: psycopg.Connection, workspace: UUID, setup: dict[str, Any]
) -> None:
    job = _export_job(conn, workspace, setup)
    conn.execute(
        "UPDATE datasets SET updated_at = now() + interval '1 second' WHERE id = %s",
        (setup["dataset"],),
    )

    with pytest.raises(PermanentJobError, match=CHANGED_DATASET):
        setup["handler"](job, threading.Event())
    assert setup["runner"].requests == []


def test_a_limit_error_from_the_sandbox_is_shown(
    conn: psycopg.Connection, workspace: UUID, setup: dict[str, Any]
) -> None:
    message = "the result has more than 1,000,000 rows; add a filter or aggregate"
    report = ExportReport(rows=0, bytes=0, columns=[], error=message)
    setup["runner"].result = ExportResult(
        status="ok", exit_code=0, stderr="", report=report,
        output_dir=None, duration_seconds=0.1,
    )  # fmt: skip
    job = _export_job(conn, workspace, setup)

    with pytest.raises(PermanentJobError, match="more than 1,000,000 rows"):
        setup["handler"](job, threading.Event())


def test_an_unavailable_runner_is_retried(
    conn: psycopg.Connection, workspace: UUID, setup: dict[str, Any]
) -> None:
    setup["runner"].result = _failed("error", UNAVAILABLE)
    job = _export_job(conn, workspace, setup)

    with pytest.raises(RetryableJobError):
        setup["handler"](job, threading.Event())


@pytest.mark.security
def test_sandbox_stderr_is_never_shown(
    conn: psycopg.Connection, workspace: UUID, setup: dict[str, Any]
) -> None:
    setup["runner"].result = _failed("error", "Traceback: SECRET-STDERR")
    job = _export_job(conn, workspace, setup)

    with pytest.raises(PermanentJobError) as caught:
        setup["handler"](job, threading.Event())
    assert "SECRET-STDERR" not in str(caught.value)


def test_a_timeout_says_what_to_change(
    conn: psycopg.Connection, workspace: UUID, setup: dict[str, Any]
) -> None:
    setup["runner"].result = _failed("timeout")
    job = _export_job(conn, workspace, setup)

    with pytest.raises(PermanentJobError, match="120 s"):
        setup["handler"](job, threading.Event())


def test_expired_exports_are_purged_on_each_export(
    conn: psycopg.Connection, workspace: UUID, setup: dict[str, Any]
) -> None:
    setup["exports"].mkdir()
    old = setup["exports"] / "old.csv"
    old.write_text("x", encoding="utf-8")
    a_day_ago = time.time() - 25 * 3600
    os.utime(old, (a_day_ago, a_day_ago))

    setup["handler"](_export_job(conn, workspace, setup), threading.Event())

    assert not old.exists()
