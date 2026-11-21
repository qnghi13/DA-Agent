"""The worker's query job: SQL a person edited, checked again and run in a sandbox.

Needs DA_AGENT_TEST_POSTGRES_URL. The Runner is a fake; the real sandbox path is the agent's.
See docs/design/2026-11-14-edit-sql.md.
"""

import os
import threading
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

from da_agent.runner.client import UNAVAILABLE
from da_agent.runner.runner import SqlResult, SqlRun
from da_agent.storage.dataset_versions import ensure_first_version
from da_agent.storage.datasets import DatasetStore
from da_agent.storage.jobs import Job, JobQueue
from da_agent.worker.queries import QueryHandler
from da_agent.worker.worker import PermanentJobError, RetryableJobError
from tests.integration.conftest import apply_next_version

pytestmark = pytest.mark.integration
URL = os.environ.get("DA_AGENT_TEST_POSTGRES_URL", "")
QUERIES = [{"sql": "SELECT id FROM sales", "columns": ["id"], "rows": [[1]], "truncated": False}]


class FakeRunner:
    def __init__(self) -> None:
        self.requests: list[SqlRun] = []
        self.result = SqlResult(status="ok", columns=["id"], rows=[[1], [2]], truncated=False)

    def query(self, request: SqlRun) -> SqlResult:
        self.requests.append(request)
        return self.result


@pytest.fixture
def setup(conn: psycopg.Connection, workspace: UUID, schema: str) -> dict[str, Any]:
    dataset = DatasetStore(conn).create(workspace, "shop", "")
    conn.execute("UPDATE datasets SET status = 'ready' WHERE id = %s", (dataset,))
    ensure_first_version(conn, dataset)
    queue = JobQueue(conn)
    run = queue.enqueue(workspace, "run", {"dataset_id": str(dataset), "question": "q"})
    conn.execute(
        "UPDATE jobs SET status = 'succeeded', result = %s WHERE id = %s",
        (Jsonb({"queries": QUERIES}), run),
    )
    runner = FakeRunner()
    handler = QueryHandler(url=URL, schema=schema, runner=runner)
    return {"dataset": dataset, "run": run, "runner": runner, "handler": handler}


def _query_job(
    conn: psycopg.Connection, workspace: UUID, setup: dict[str, Any], sql: str, **payload: Any
) -> Job:
    body = {"run_id": str(setup["run"]), "dataset_id": str(setup["dataset"]), "sql": sql}
    job_id = JobQueue(conn).enqueue(workspace, "query", body | payload)
    job = JobQueue(conn).get(job_id, workspace)
    assert job is not None
    return job


def test_a_query_runs_in_the_sandbox_and_keeps_its_rows(
    conn: psycopg.Connection, workspace: UUID, setup: dict[str, Any]
) -> None:
    job = _query_job(conn, workspace, setup, "SELECT id FROM sales WHERE id > 0;")

    result = setup["handler"](job, threading.Event())

    assert setup["runner"].requests == [
        SqlRun(dataset_id=str(setup["dataset"]), sql="SELECT id FROM sales WHERE id > 0")
    ]
    assert result == {
        "sql": "SELECT id FROM sales WHERE id > 0",
        "columns": ["id"],
        "rows": [[1], [2]],
        "truncated": False,
    }


def test_an_engine_error_reaches_the_person_as_is(
    conn: psycopg.Connection, workspace: UUID, setup: dict[str, Any]
) -> None:
    message = 'Binder Error: Referenced column "totl" not found. Candidate bindings: "total"'
    setup["runner"].result = SqlResult(status="error", error=message)
    job = _query_job(conn, workspace, setup, "SELECT totl FROM sales")

    with pytest.raises(PermanentJobError, match="Candidate bindings"):
        setup["handler"](job, threading.Event())


def test_a_missing_runner_is_retried(
    conn: psycopg.Connection, workspace: UUID, setup: dict[str, Any]
) -> None:
    setup["runner"].result = SqlResult(status="error", error=UNAVAILABLE)
    job = _query_job(conn, workspace, setup, "SELECT id FROM sales")

    with pytest.raises(RetryableJobError):
        setup["handler"](job, threading.Event())


@pytest.mark.parametrize(
    ("status", "words"), [("timeout", "longer than 15 s"), ("killed", "out of memory")]
)
def test_limits_fail_with_a_fixed_message(
    conn: psycopg.Connection, workspace: UUID, setup: dict[str, Any], status: str, words: str
) -> None:
    setup["runner"].result = SqlResult(status=status, error="query stopped")
    job = _query_job(conn, workspace, setup, "SELECT id FROM sales")

    with pytest.raises(PermanentJobError, match=words):
        setup["handler"](job, threading.Event())


@pytest.mark.security
@pytest.mark.parametrize(
    "sql", ["DROP TABLE sales", "SELECT 1; DELETE FROM sales", "SELECT * FROM read_csv('/etc/x')"]
)
def test_forbidden_sql_in_the_payload_never_reaches_the_sandbox(
    conn: psycopg.Connection, workspace: UUID, setup: dict[str, Any], sql: str
) -> None:
    job = _query_job(conn, workspace, setup, sql)

    with pytest.raises(PermanentJobError):
        setup["handler"](job, threading.Event())
    assert setup["runner"].requests == []


def test_a_dataset_that_is_not_ready_is_refused(
    conn: psycopg.Connection, workspace: UUID, setup: dict[str, Any]
) -> None:
    conn.execute("UPDATE datasets SET status = 'building' WHERE id = %s", (setup["dataset"],))
    job = _query_job(conn, workspace, setup, "SELECT id FROM sales")

    with pytest.raises(PermanentJobError, match="not ready"):
        setup["handler"](job, threading.Event())
    assert setup["runner"].requests == []


@pytest.mark.security
def test_a_dataset_other_than_the_runs_is_not_found(
    conn: psycopg.Connection, workspace: UUID, setup: dict[str, Any]
) -> None:
    other = DatasetStore(conn).create(workspace, "other", "")
    conn.execute("UPDATE datasets SET status = 'ready' WHERE id = %s", (other,))
    job = _query_job(conn, workspace, setup, "SELECT 1", dataset_id=str(other))

    with pytest.raises(PermanentJobError, match="not found"):
        setup["handler"](job, threading.Event())
    assert setup["runner"].requests == []


def test_an_unknown_run_is_not_found(
    conn: psycopg.Connection, workspace: UUID, setup: dict[str, Any]
) -> None:
    job = _query_job(conn, workspace, setup, "SELECT 1", run_id=str(uuid4()))

    with pytest.raises(PermanentJobError, match="not found"):
        setup["handler"](job, threading.Event())


def _run_on(conn: psycopg.Connection, workspace: UUID, dataset: UUID, **payload: Any) -> UUID:
    run = JobQueue(conn).enqueue(
        workspace, "run", {"dataset_id": str(dataset), "question": "q"} | payload
    )
    conn.execute(
        "UPDATE jobs SET status = 'succeeded', result = %s WHERE id = %s",
        (Jsonb({"queries": QUERIES}), run),
    )
    return run


def test_query_job_uses_run_version(
    conn: psycopg.Connection, workspace: UUID, setup: dict[str, Any]
) -> None:
    dataset = setup["dataset"]
    apply_next_version(conn, workspace, dataset)
    old = _run_on(conn, workspace, dataset, dataset_version=1)
    new = _run_on(conn, workspace, dataset, dataset_version=2)

    for run, directory in ((old, str(dataset)), (new, f"{dataset}-v2")):
        setup["runner"].requests.clear()
        job = _query_job(conn, workspace, setup, "SELECT id FROM sales", run_id=str(run))
        setup["handler"](job, threading.Event())
        assert [r.dataset_id for r in setup["runner"].requests] == [directory]


def test_a_run_without_a_version_queries_version_one(
    conn: psycopg.Connection, workspace: UUID, setup: dict[str, Any]
) -> None:
    apply_next_version(conn, workspace, setup["dataset"])

    # The fixture's run has no dataset_version in its payload.
    setup["handler"](_query_job(conn, workspace, setup, "SELECT id FROM sales"), threading.Event())

    assert [r.dataset_id for r in setup["runner"].requests] == [str(setup["dataset"])]


def test_a_run_on_a_version_that_is_not_applied_is_refused(
    conn: psycopg.Connection, workspace: UUID, setup: dict[str, Any]
) -> None:
    run = _run_on(conn, workspace, setup["dataset"], dataset_version=7)
    job = _query_job(conn, workspace, setup, "SELECT id FROM sales", run_id=str(run))

    with pytest.raises(PermanentJobError, match="not found"):
        setup["handler"](job, threading.Event())
    assert setup["runner"].requests == []
