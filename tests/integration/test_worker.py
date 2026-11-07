"""The worker: claims jobs, keeps their lease, runs the agent graph, and survives being killed.

Needs a Postgres server. Set DA_AGENT_TEST_POSTGRES_URL, or the tests are skipped.
"""

import os
import sqlite3
import time
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
import pytest

from da_agent.agent.answer import result_from_json
from da_agent.agent.loop_types import Document
from da_agent.llm.client import CostLedger, LLMClient, ModelPrice, Route
from da_agent.llm.types import TransientLLMError
from da_agent.storage.db import connect
from da_agent.storage.events import RunEvents
from da_agent.storage.jobs import Job, JobQueue
from da_agent.storage.ledger import PostgresCostLedger, spent_today
from da_agent.storage.sqlite_dataset import SqliteDataset
from da_agent.storage.workspaces import set_daily_budget
from da_agent.worker.limits import WorkspaceBudgets
from da_agent.worker.runs import RunHandler
from da_agent.worker.worker import PermanentJobError, RetryableJobError, Worker, WorkerConfig
from tests.unit.test_agent_graph import ScriptedAdapter, plan, sql, submit

URL = os.environ.get("DA_AGENT_TEST_POSTGRES_URL")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not URL, reason="DA_AGENT_TEST_POSTGRES_URL is not set"),
]


class Killed(BaseException):
    """Stands in for the worker process dying: nothing in the worker catches it."""


@pytest.fixture
def dataset(tmp_path: Path) -> SqliteDataset:
    path = tmp_path / "shop.sqlite"
    connection = sqlite3.connect(path)
    connection.executescript(
        "CREATE TABLE orders (id INTEGER, total INTEGER, note TEXT);"
        "INSERT INTO orders VALUES (1, 100, 'a'); INSERT INTO orders VALUES (2, 250, 'b');"
    )
    connection.commit()
    connection.close()
    return SqliteDataset(path)


def config(schema: str, worker_id: str, **overrides: Any) -> WorkerConfig:
    assert URL is not None
    settings: dict[str, Any] = {
        "url": URL,
        "schema": schema,
        "worker_id": worker_id,
        "lease_seconds": 30,
        "heartbeat_seconds": 10,
        "retry_delays": (60,),
    }
    return WorkerConfig(**(settings | overrides))


def run_handler(adapter: ScriptedAdapter, dataset: SqliteDataset, schema: str) -> RunHandler:
    def datasets(workspace_id: UUID, dataset_id: str):
        assert dataset_id == "shop"
        return dataset, [Document("rules", "Revenue is the sum of totals.")]

    llm = LLMClient([Route(adapter, "m")], CostLedger(), sleep=lambda _: None, max_retries=0)
    assert URL is not None
    return RunHandler(llm=llm, datasets=datasets, checkpoint_url=URL, schema=schema)


def enqueue_run(conn: psycopg.Connection, workspace: UUID) -> UUID:
    payload = {"question": "Total revenue?", "as_of": "2026-06-28", "dataset_id": "shop"}
    return JobQueue(conn).enqueue(workspace, "run", payload)


def job(conn: psycopg.Connection, job_id: UUID, workspace: UUID) -> Job:
    found = JobQueue(conn).get(job_id, workspace)
    assert found is not None
    return found


def test_a_run_job_answers_and_stores_the_result(
    schema: str, conn: psycopg.Connection, workspace: UUID, dataset: SqliteDataset
) -> None:
    job_id = enqueue_run(conn, workspace)
    adapter = ScriptedAdapter([plan(), sql(), submit("number", 350)])
    worker = Worker(config(schema, "w1"), {"run": run_handler(adapter, dataset, schema)})

    assert worker.run_once()

    done = job(conn, job_id, workspace)
    assert done.status == "succeeded" and done.result is not None
    result = result_from_json(done.result)
    assert (result.stop_reason, result.answer and result.answer.value) == ("answered", 350)
    assert result.queries[0].rows == [[350]]
    assert not worker.run_once()


def test_a_killed_worker_is_resumed_by_another_without_repeating_finished_turns(
    schema: str, conn: psycopg.Connection, workspace: UUID, dataset: SqliteDataset
) -> None:
    job_id = enqueue_run(conn, workspace)
    adapter = ScriptedAdapter([plan(), sql(), Killed(), submit("number", 350)])
    handler = run_handler(adapter, dataset, schema)
    with pytest.raises(Killed):
        Worker(config(schema, "dead"), {"run": handler}).run_once()
    conn.execute("UPDATE jobs SET lease_until = now() - interval '1 second'")

    assert Worker(config(schema, "alive"), {"run": handler}).run_once()

    done = job(conn, job_id, workspace)
    assert (done.status, done.attempts) == ("succeeded", 2)
    # plan, sql, the killed turn, and the answer: no finished turn ran twice.
    assert len(adapter.requests) == 4


def test_a_provider_failure_requeues_the_run_and_the_retry_continues_it(
    schema: str, conn: psycopg.Connection, workspace: UUID, dataset: SqliteDataset
) -> None:
    job_id = enqueue_run(conn, workspace)
    adapter = ScriptedAdapter(
        [plan(), TransientLLMError("timed out"), sql(), submit("number", 350)]
    )
    worker = Worker(config(schema, "w1"), {"run": run_handler(adapter, dataset, schema)})

    assert worker.run_once()
    waiting = job(conn, job_id, workspace)
    assert waiting.status == "queued" and "provider" in (waiting.last_error or "")
    assert not worker.run_once()  # the retry waits for its delay
    conn.execute("UPDATE jobs SET run_after = now()")

    assert worker.run_once()
    done = job(conn, job_id, workspace)
    assert done.status == "succeeded"
    assert len(adapter.requests) == 4


def test_an_unexpected_crash_fails_the_job_without_leaking_its_message(
    schema: str, conn: psycopg.Connection, workspace: UUID
) -> None:
    job_id = JobQueue(conn).enqueue(workspace, "run", {})

    def crash(job: Job, lost: Any) -> dict[str, Any]:
        raise RuntimeError("cannot open /srv/secret/datasets/other-workspace.duckdb")

    assert Worker(config(schema, "w1"), {"run": crash}).run_once()

    failed = job(conn, job_id, workspace)
    assert failed.status == "failed"
    assert "RuntimeError" in (failed.last_error or "")
    assert "/srv/secret" not in (failed.last_error or "")


def test_a_retryable_error_waits_with_backoff(
    schema: str, conn: psycopg.Connection, workspace: UUID
) -> None:
    job_id = JobQueue(conn).enqueue(workspace, "run", {})

    def busy(job: Job, lost: Any) -> dict[str, Any]:
        raise RetryableJobError("the sandbox is busy")

    Worker(config(schema, "w1", retry_delays=(120, 600)), {"run": busy}).run_once()

    waiting = job(conn, job_id, workspace)
    assert (waiting.status, waiting.last_error) == ("queued", "the sandbox is busy")
    delay = conn.execute("SELECT run_after - now() > interval '100 seconds' FROM jobs").fetchone()
    assert delay == (True,)


def test_a_job_of_unknown_kind_fails(
    schema: str, conn: psycopg.Connection, workspace: UUID
) -> None:
    job_id = JobQueue(conn).enqueue(workspace, "ingest", {})

    Worker(config(schema, "w1"), {}).run_once()

    assert job(conn, job_id, workspace).status == "failed"


def test_the_heartbeat_keeps_a_long_job_leased(
    schema: str, conn: psycopg.Connection, workspace: UUID
) -> None:
    job_id = JobQueue(conn).enqueue(workspace, "run", {})
    seen: dict[str, Any] = {}

    def slow(job: Job, lost: Any) -> dict[str, Any]:
        time.sleep(2.5)  # longer than the one-second lease
        with connect(URL or "", schema=schema) as other:
            seen["stolen"] = JobQueue(other).claim("thief", lease_seconds=30)
        seen["lost"] = lost.is_set()
        return {"ok": True}

    worker = Worker(config(schema, "w1", lease_seconds=1, heartbeat_seconds=0.2), {"run": slow})

    assert worker.run_once()
    assert seen == {"stolen": None, "lost": False}
    assert job(conn, job_id, workspace).status == "succeeded"


def test_a_worker_that_lost_its_job_does_not_write_a_result(
    schema: str, conn: psycopg.Connection, workspace: UUID
) -> None:
    job_id = JobQueue(conn).enqueue(workspace, "run", {})

    def overtaken(job: Job, lost: Any) -> dict[str, Any]:
        with connect(URL or "", schema=schema) as other:
            other.execute("UPDATE jobs SET lease_until = now() - interval '1 second'")
            JobQueue(other).claim("other", lease_seconds=30)
        return {"stale": True}

    Worker(config(schema, "w1"), {"run": overtaken}).run_once()

    current = job(conn, job_id, workspace)
    assert (current.status, current.result) == ("running", None)


def test_a_permanent_error_fails_the_job_with_its_message(
    schema: str, conn: psycopg.Connection, workspace: UUID
) -> None:
    job_id = JobQueue(conn).enqueue(workspace, "run", {})

    def gone(job: Job, lost: Any) -> dict[str, Any]:
        raise PermanentJobError("the dataset is not ready")

    Worker(config(schema, "w1"), {"run": gone}).run_once()

    failed = job(conn, job_id, workspace)
    assert (failed.status, failed.last_error) == ("failed", "the dataset is not ready")


def test_a_run_job_records_its_progress_events(
    schema: str, conn: psycopg.Connection, workspace: UUID, dataset: SqliteDataset
) -> None:
    job_id = enqueue_run(conn, workspace)
    adapter = ScriptedAdapter([plan(), sql(), submit("number", 350)])

    Worker(config(schema, "w1"), {"run": run_handler(adapter, dataset, schema)}).run_once()

    events = RunEvents(conn).after(job_id, workspace, after_id=0)
    assert [e.type for e in events] == ["plan", "turn", "tool", "turn", "tool", "verify", "done"]
    assert events[2].data["sql"] == "SELECT SUM(total) AS t FROM orders"
    assert [e.id for e in events] == sorted(e.id for e in events)


def test_a_run_stops_at_the_money_its_workspace_has_left(
    schema: str, conn: psycopg.Connection, workspace: UUID, dataset: SqliteDataset
) -> None:
    assert URL is not None
    job_id = enqueue_run(conn, workspace)
    adapter = ScriptedAdapter([plan()] + [sql()] * 5 + [submit("number", 350)])
    price = ModelPrice(input_per_million=1000.0, output_per_million=1000.0)  # 0.015 per call
    with connect(URL, schema=schema) as ledger_conn:
        llm = LLMClient(
            [Route(adapter, "m", price)], PostgresCostLedger(ledger_conn), sleep=lambda _: None
        )
        handler = run_handler(adapter, dataset, schema)
        handler.llm = llm
        handler.budgets = WorkspaceBudgets(url=URL, schema=schema, daily_usd=0.04, run_usd=1.0)

        Worker(config(schema, "w1"), {"run": handler}).run_once()

    done = job(conn, job_id, workspace)
    assert done.result is not None and done.result["stop_reason"] == "budget_exceeded"
    assert spent_today(conn, workspace) < 0.04 + 0.016


def test_a_workspace_without_budget_left_gets_a_clear_failure(
    schema: str, conn: psycopg.Connection, workspace: UUID, dataset: SqliteDataset
) -> None:
    assert URL is not None
    job_id = enqueue_run(conn, workspace)
    set_daily_budget(conn, workspace, 0.0)
    adapter = ScriptedAdapter([plan(), sql(), submit("number", 350)])
    handler = run_handler(adapter, dataset, schema)
    handler.budgets = WorkspaceBudgets(url=URL, schema=schema, daily_usd=1.0, run_usd=1.0)

    Worker(config(schema, "w1"), {"run": handler}).run_once()

    failed = job(conn, job_id, workspace)
    assert failed.status == "failed" and "daily model budget" in (failed.last_error or "")
    assert adapter.requests == []
