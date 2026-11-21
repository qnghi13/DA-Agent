"""The worker: claims jobs, keeps their lease, runs the agent graph, and survives being killed.

Needs a Postgres server. Set DA_AGENT_TEST_POSTGRES_URL, or the tests are skipped.
"""

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import format_trace_id

from da_agent.agent.answer import result_from_json
from da_agent.agent.loop_types import Document
from da_agent.llm.client import CostLedger, LLMClient, ModelPrice, Route
from da_agent.llm.types import TransientLLMError
from da_agent.storage.db import connect
from da_agent.storage.events import RunEvents
from da_agent.storage.jobs import Job, JobQueue
from da_agent.storage.ledger import PostgresCostLedger, spent_today
from da_agent.storage.sqlite_dataset import SqliteDataset
from da_agent.storage.versions import Attempt, attempts_of, register_version
from da_agent.storage.workspaces import set_daily_budget
from da_agent.telemetry.tracing import configure_tracing
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


def run_handler(
    adapter: ScriptedAdapter,
    dataset: SqliteDataset,
    schema: str,
    agent_version: str | None = None,
) -> RunHandler:
    def datasets(workspace_id: UUID, dataset_id: str):
        assert dataset_id == "shop"
        return dataset, [Document("rules", "Revenue is the sum of totals.")]

    llm = LLMClient([Route(adapter, "m")], CostLedger(), sleep=lambda _: None, max_retries=0)
    assert URL is not None
    return RunHandler(
        llm=llm, datasets=datasets, checkpoint_url=URL, schema=schema, agent_version=agent_version
    )


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


def test_each_attempt_records_the_trace_it_ran_in(
    schema: str, conn: psycopg.Connection, workspace: UUID
) -> None:
    exporter = InMemorySpanExporter()
    provider = configure_tracing("http://unused", exporter=exporter)
    job_id = JobQueue(conn).enqueue(workspace, "run", {})

    def busy(job: Job, lost: Any) -> dict[str, Any]:
        raise RetryableJobError("the sandbox is busy")

    worker = Worker(
        config(schema, "w1", retry_delays=(0,)), {"run": busy}, tracer=provider.get_tracer("t")
    )
    assert worker.run_once() and worker.run_once()
    provider.force_flush()

    roots = [s for s in exporter.get_finished_spans() if s.name == "job.run"]
    assert len(roots) == 2
    expected = [format_trace_id(span.context.trace_id) for span in roots if span.context]
    assert job(conn, job_id, workspace).trace_ids == expected


def test_without_tracing_no_trace_is_recorded(
    schema: str, conn: psycopg.Connection, workspace: UUID
) -> None:
    job_id = JobQueue(conn).enqueue(workspace, "run", {})
    worker = Worker(config(schema, "w1"), {"run": lambda job, lost: {}}, tracer=trace.NoOpTracer())

    assert worker.run_once()

    assert job(conn, job_id, workspace).trace_ids == []


def test_each_attempt_is_recorded_with_its_worker_version_and_trace(
    schema: str, conn: psycopg.Connection, workspace: UUID
) -> None:
    exporter = InMemorySpanExporter()
    provider = configure_tracing("http://unused", exporter=exporter)
    register_version(conn, "v1", {})
    job_id = JobQueue(conn).enqueue(workspace, "run", {})

    def busy(job: Job, lost: Any) -> dict[str, Any]:
        raise RetryableJobError("the sandbox is busy")

    worker = Worker(
        config(schema, "w1", retry_delays=(0,), agent_version="v1"),
        {"run": busy},
        tracer=provider.get_tracer("t"),
    )
    assert worker.run_once() and worker.run_once()
    provider.force_flush()

    attempts = attempts_of(conn, job_id)
    assert [(a.attempt, a.worker_id, a.agent_version) for a in attempts] == [
        (1, "w1", "v1"),
        (2, "w1", "v1"),
    ]
    assert [a.trace_id for a in attempts] == job(conn, job_id, workspace).trace_ids
    root = next(s for s in exporter.get_finished_spans() if s.name == "job.run")
    assert root.attributes is not None
    assert root.attributes["da_agent.agent_version"] == "v1"
    assert root.attributes["gen_ai.agent.version"] == "v1"


def test_without_tracing_the_attempt_is_still_recorded(
    schema: str, conn: psycopg.Connection, workspace: UUID
) -> None:
    job_id = JobQueue(conn).enqueue(workspace, "run", {})
    worker = Worker(config(schema, "w1"), {"run": lambda job, lost: {}}, tracer=trace.NoOpTracer())

    assert worker.run_once()

    assert attempts_of(conn, job_id) == [Attempt(1, "w1", None, None, None)]


def resumed_marks(
    conn: psycopg.Connection,
    workspace: UUID,
    dataset: SqliteDataset,
    schema: str,
    first: str | None,
    second: str | None,
) -> list[str | None]:
    """Kill a run after plan and sql under version first, resume it under second."""
    for version in {first, second} - {None}:
        register_version(conn, version, {})
    job_id = enqueue_run(conn, workspace)
    adapter = ScriptedAdapter([plan(), sql(), Killed(), submit("number", 350)])
    with pytest.raises(Killed):
        Worker(
            config(schema, "dead", agent_version=first),
            {"run": run_handler(adapter, dataset, schema, first)},
        ).run_once()
    conn.execute("UPDATE jobs SET lease_until = now() - interval '1 second'")

    assert Worker(
        config(schema, "alive", agent_version=second),
        {"run": run_handler(adapter, dataset, schema, second)},
    ).run_once()

    assert job(conn, job_id, workspace).status == "succeeded"
    return [a.resumed_from_version for a in attempts_of(conn, job_id)]


def test_a_run_resumed_by_another_version_is_marked(
    schema: str, conn: psycopg.Connection, workspace: UUID, dataset: SqliteDataset
) -> None:
    assert resumed_marks(conn, workspace, dataset, schema, "old", "new") == [None, "old"]


def test_a_run_resumed_by_the_same_version_is_not_marked(
    schema: str, conn: psycopg.Connection, workspace: UUID, dataset: SqliteDataset
) -> None:
    assert resumed_marks(conn, workspace, dataset, schema, "v1", "v1") == [None, None]


def test_a_checkpoint_from_before_versions_is_not_marked(
    schema: str, conn: psycopg.Connection, workspace: UUID, dataset: SqliteDataset
) -> None:
    assert resumed_marks(conn, workspace, dataset, schema, None, "new") == [None, None]


def test_a_failed_attempt_record_never_fails_the_job(
    schema: str, conn: psycopg.Connection, workspace: UUID
) -> None:
    job_id = JobQueue(conn).enqueue(workspace, "run", {})
    # A version the database does not know makes the attempt insert fail.
    worker = Worker(
        config(schema, "w1", agent_version="unregistered"), {"run": lambda job, lost: {"ok": 1}}
    )

    assert worker.run_once()

    assert job(conn, job_id, workspace).status == "succeeded"
    assert attempts_of(conn, job_id) == []


def test_a_failed_resume_mark_never_fails_the_run(
    schema: str,
    conn: psycopg.Connection,
    workspace: UUID,
    dataset: SqliteDataset,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(*args: Any) -> None:
        raise psycopg.OperationalError("the database went away")

    monkeypatch.setattr("da_agent.worker.runs.mark_resumed_from", broken)

    assert resumed_marks(conn, workspace, dataset, schema, "old", "new") == [None, None]


def test_a_run_job_gives_the_model_its_memory_after_the_dataset_documents(
    schema: str, conn: psycopg.Connection, workspace: UUID, dataset: SqliteDataset
) -> None:
    job_id = enqueue_run(conn, workspace)
    adapter = ScriptedAdapter([plan(), sql(), submit("number", 350)])
    handler = run_handler(adapter, dataset, schema)
    seen: list[UUID] = []
    tables: list[dict[str, set[str]]] = []

    def memory(claimed: Job, visible: dict[str, set[str]]) -> list[Document]:
        seen.append(claimed.id)
        tables.append(visible)
        return [Document("notes", "Revenue excludes cancelled orders.")]

    handler.memory = memory
    assert Worker(config(schema, "w1"), {"run": handler}).run_once()

    assert seen == [job_id]
    assert tables == [{t.name: {c.name for c in t.columns} for t in dataset.describe()}]
    system = adapter.requests[0].messages[0].content or ""
    assert system.index("Revenue is the sum of totals.") < system.index(
        "Revenue excludes cancelled orders."
    )


def test_a_run_cancelled_while_running_stops_and_ends_cancelled(
    schema: str, conn: psycopg.Connection, workspace: UUID, dataset: SqliteDataset
) -> None:
    from tests.unit.test_agent_graph import StoppingAdapter

    job_id = enqueue_run(conn, workspace)
    script = [plan(), sql(), sql(), submit("number", 350)]
    adapter = StoppingAdapter(script, 3, lambda: JobQueue(conn).request_cancel(job_id, workspace))
    handler = run_handler(adapter, dataset, schema)
    handler.cancel_poll_seconds = 0.0

    assert Worker(config(schema, "w1"), {"run": handler}).run_once()

    done = job(conn, job_id, workspace)
    assert done.status == "cancelled" and done.result is not None
    assert result_from_json(done.result).stop_reason == "cancelled"
    assert len(adapter.requests) == 3


def test_a_run_whose_lease_is_lost_stops_without_calling_the_model(
    schema: str, conn: psycopg.Connection, workspace: UUID, dataset: SqliteDataset
) -> None:
    import threading

    job_id = enqueue_run(conn, workspace)
    claimed = JobQueue(conn).claim("w1", lease_seconds=30)
    assert claimed is not None and claimed.id == job_id
    adapter = ScriptedAdapter([plan(), sql(), submit("number", 350)])
    lost = threading.Event()
    lost.set()

    result = run_handler(adapter, dataset, schema)(claimed, lost)

    assert result["stop_reason"] == "lease_lost"
    assert adapter.requests == []


def test_a_worker_that_lost_its_lease_does_not_complete_the_job(
    schema: str, conn: psycopg.Connection, workspace: UUID
) -> None:
    job_id = JobQueue(conn).enqueue(workspace, "run", {})

    def lost_it(job: Job, lost: Any) -> dict[str, Any]:
        lost.set()
        return {"stop_reason": "lease_lost"}

    Worker(config(schema, "w1"), {"run": lost_it}, tracer=trace.NoOpTracer()).run_once()

    assert job(conn, job_id, workspace).status == "running"


def test_an_operator_forks_a_finished_run_from_one_of_its_steps(
    schema: str, conn: psycopg.Connection, workspace: UUID, dataset: SqliteDataset
) -> None:
    from langgraph.checkpoint.postgres import PostgresSaver

    from da_agent.timetravel import copy_checkpoint, list_checkpoints

    job_id = enqueue_run(conn, workspace)
    first = ScriptedAdapter([plan(), sql(), submit("number", 350)])
    Worker(config(schema, "w1"), {"run": run_handler(first, dataset, schema)}).run_once()
    before = job(conn, job_id, workspace)
    second = ScriptedAdapter([sql(), submit("number", 350)])
    handler = run_handler(second, dataset, schema)

    with handler.checkpoint_connection() as saver_conn:
        saver = PostgresSaver(saver_conn)
        rows = list_checkpoints(saver, str(job_id))
        after_plan = next(row for row in rows if "plan" in row.ran)
        copy_checkpoint(saver, str(job_id), after_plan.checkpoint_id, "fork-1")
        result = handler.fork(before, "fork-1")
        assert list_checkpoints(saver, str(job_id))[-1].checkpoint_id == rows[-1].checkpoint_id

    assert result["stop_reason"] == "answered"
    assert len(second.requests) == 2
    assert job(conn, job_id, workspace) == before


def test_the_time_travel_cli_lists_and_shows_a_run(
    schema: str, conn: psycopg.Connection, workspace: UUID, dataset: SqliteDataset,
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:  # fmt: skip
    from psycopg.conninfo import make_conninfo

    from da_agent.timetravel import main

    job_id = enqueue_run(conn, workspace)
    adapter = ScriptedAdapter([plan(), sql(), submit("number", 350)])
    Worker(config(schema, "w1"), {"run": run_handler(adapter, dataset, schema)}).run_once()
    assert URL is not None
    monkeypatch.setenv(
        "DA_AGENT_DATABASE_URL", make_conninfo(URL, options=f"-c search_path={schema}")
    )
    monkeypatch.setenv("DA_AGENT_DATA_DIR", str(tmp_path))

    assert main(["list", str(job_id), "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert rows[-1]["stop_reason"] == "answered"
    assert main(["show", str(job_id), rows[-1]["checkpoint_id"], "--max-chars", "30"]) == 0
    assert json.loads(capsys.readouterr().out)["stop_reason"] == "answered"
    assert main(["show", str(job_id), "missing"]) == 1
    assert "no checkpoint" in capsys.readouterr().err
    assert main(["list", "no-such-run"]) == 1


def metric(registry: Any, name: str, **labels: str) -> float:
    found = registry.get_sample_value(name, labels)
    return 0.0 if found is None else found


@pytest.mark.parametrize(
    ("handler_result", "attempt", "outcome"),
    [
        (
            {"stop_reason": "answered", "answer": {"kind": "number", "value": 1}},
            "succeeded",
            "answered",
        ),
        ({"stop_reason": "llm_error"}, "succeeded", "model_error"),
        ({"stop_reason": "cancelled"}, "cancelled", "cancelled"),
        (PermanentJobError("no dataset"), "failed", "failed"),
    ],
)
def test_finished_run_jobs_are_counted_for_the_slos(
    schema: str, conn: psycopg.Connection, workspace: UUID,
    handler_result: Any, attempt: str, outcome: str,
) -> None:  # fmt: skip
    from prometheus_client import CollectorRegistry

    from da_agent.telemetry.metrics import WorkerMetrics

    JobQueue(conn).enqueue(workspace, "run", {})
    registry = CollectorRegistry()

    def handle(job: Job, lost: Any) -> dict[str, Any]:
        if isinstance(handler_result, Exception):
            raise handler_result
        return handler_result

    worker = Worker(
        config(schema, "w1"), {"run": handle}, tracer=trace.NoOpTracer(),
        metrics=WorkerMetrics(registry),
    )  # fmt: skip
    worker.run_once()

    assert metric(registry, "da_agent_runs_total", outcome=outcome) == 1
    assert metric(registry, "da_agent_job_attempts_total", kind="run", result=attempt) == 1
    assert metric(registry, "da_agent_run_duration_seconds_count") == 1
    assert metric(registry, "da_agent_queue_wait_seconds_count") == 1


def test_retries_count_as_attempts_and_wait_only_once(
    schema: str, conn: psycopg.Connection, workspace: UUID
) -> None:
    from prometheus_client import CollectorRegistry

    from da_agent.telemetry.metrics import WorkerMetrics

    JobQueue(conn).enqueue(workspace, "run", {})
    registry = CollectorRegistry()
    results: list[Any] = [RetryableJobError("provider down"), {"stop_reason": "answered"}]

    def handle(job: Job, lost: Any) -> dict[str, Any]:
        step = results.pop(0)
        if isinstance(step, Exception):
            raise step
        return step

    worker = Worker(
        config(schema, "w1", retry_delays=(0,)), {"run": handle}, tracer=trace.NoOpTracer(),
        metrics=WorkerMetrics(registry),
    )  # fmt: skip
    worker.run_once()
    worker.run_once()

    assert metric(registry, "da_agent_job_attempts_total", kind="run", result="retry") == 1
    assert metric(registry, "da_agent_job_attempts_total", kind="run", result="succeeded") == 1
    assert metric(registry, "da_agent_runs_total", outcome="answered") == 1
    assert metric(registry, "da_agent_queue_wait_seconds_count") == 1


def test_a_lost_job_is_an_attempt_but_not_a_finished_run(
    schema: str, conn: psycopg.Connection, workspace: UUID
) -> None:
    from prometheus_client import CollectorRegistry

    from da_agent.telemetry.metrics import WorkerMetrics

    JobQueue(conn).enqueue(workspace, "run", {})
    registry = CollectorRegistry()

    def lose(job: Job, lost: Any) -> dict[str, Any]:
        lost.set()
        return {"stop_reason": "lease_lost"}

    Worker(
        config(schema, "w1"), {"run": lose}, tracer=trace.NoOpTracer(),
        metrics=WorkerMetrics(registry),
    ).run_once()  # fmt: skip

    assert metric(registry, "da_agent_job_attempts_total", kind="run", result="lost") == 1
    assert metric(registry, "da_agent_runs_total", outcome="answered") == 0


def test_a_run_whose_worker_died_on_its_last_attempt_counts_as_failed(
    schema: str, conn: psycopg.Connection, workspace: UUID
) -> None:
    from prometheus_client import CollectorRegistry

    from da_agent.telemetry.metrics import WorkerMetrics

    queue = JobQueue(conn)
    queue.enqueue(workspace, "run", {}, max_attempts=1)
    assert queue.claim("dead", lease_seconds=30) is not None
    conn.execute("UPDATE jobs SET lease_until = now() - interval '1 second'")
    registry = CollectorRegistry()

    Worker(
        config(schema, "w1"), {"run": lambda job, lost: {}}, tracer=trace.NoOpTracer(),
        metrics=WorkerMetrics(registry),
    ).run_once()  # fmt: skip

    assert metric(registry, "da_agent_runs_total", outcome="failed") == 1
    assert metric(registry, "da_agent_job_attempts_total", kind="run", result="lost") == 1


def test_a_strange_result_never_crashes_the_worker_loop(
    schema: str, conn: psycopg.Connection, workspace: UUID
) -> None:
    from prometheus_client import CollectorRegistry

    from da_agent.telemetry.metrics import WorkerMetrics

    JobQueue(conn).enqueue(workspace, "run", {})
    odd = {"stop_reason": "answered", "answer": "42"}
    worker = Worker(
        config(schema, "w1"), {"run": lambda job, lost: odd},
        tracer=trace.NoOpTracer(), metrics=WorkerMetrics(CollectorRegistry()),
    )  # fmt: skip

    assert worker.run_once()


@pytest.mark.parametrize("error", [PermanentJobError("no data"), RetryableJobError("down")])
def test_a_failure_after_a_cancel_counts_as_cancelled_like_the_database(
    schema: str, conn: psycopg.Connection, workspace: UUID, error: Exception
) -> None:
    from prometheus_client import CollectorRegistry

    from da_agent.telemetry.metrics import WorkerMetrics

    job_id = JobQueue(conn).enqueue(workspace, "run", {})
    registry = CollectorRegistry()

    def cancelled_then_fails(job: Job, lost: Any) -> dict[str, Any]:
        JobQueue(conn).request_cancel(job_id, workspace)
        raise error

    Worker(
        config(schema, "w1"), {"run": cancelled_then_fails}, tracer=trace.NoOpTracer(),
        metrics=WorkerMetrics(registry),
    ).run_once()  # fmt: skip

    assert job(conn, job_id, workspace).status == "cancelled"
    assert metric(registry, "da_agent_runs_total", outcome="cancelled") == 1
    assert metric(registry, "da_agent_runs_total", outcome="failed") == 0


def test_a_fork_of_a_run_that_is_gone_leaves_no_thread_behind(
    schema: str, conn: psycopg.Connection, workspace: UUID, dataset: SqliteDataset,
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:  # fmt: skip
    from psycopg.conninfo import make_conninfo

    from da_agent.timetravel import main

    job_id = enqueue_run(conn, workspace)
    adapter = ScriptedAdapter([plan(), sql(), submit("number", 350)])
    Worker(config(schema, "w1"), {"run": run_handler(adapter, dataset, schema)}).run_once()
    assert URL is not None
    url = make_conninfo(URL, options=f"-c search_path={schema}")
    monkeypatch.setenv("DA_AGENT_DATABASE_URL", url)
    monkeypatch.setenv("DA_AGENT_DATA_DIR", str(tmp_path))
    assert main(["list", str(job_id), "--json"]) == 0
    checkpoint = json.loads(capsys.readouterr().out)[1]["checkpoint_id"]
    conn.execute("DELETE FROM jobs WHERE id = %s", (job_id,))

    assert main(["fork", str(job_id), checkpoint]) == 1

    forks = conn.execute("SELECT count(*) FROM checkpoints WHERE thread_id LIKE 'fork-%'")
    assert forks.fetchone() == (0,)
    assert "no run" in capsys.readouterr().err


def test_the_checkpoint_list_shows_when_each_step_was_saved(
    schema: str, conn: psycopg.Connection, workspace: UUID, dataset: SqliteDataset,
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:  # fmt: skip
    from psycopg.conninfo import make_conninfo

    from da_agent.timetravel import main

    job_id = enqueue_run(conn, workspace)
    adapter = ScriptedAdapter([plan(), sql(), submit("number", 350)])
    Worker(config(schema, "w1"), {"run": run_handler(adapter, dataset, schema)}).run_once()
    assert URL is not None
    monkeypatch.setenv(
        "DA_AGENT_DATABASE_URL", make_conninfo(URL, options=f"-c search_path={schema}")
    )
    monkeypatch.setenv("DA_AGENT_DATA_DIR", str(tmp_path))

    assert main(["list", str(job_id)]) == 0

    header, first = capsys.readouterr().out.splitlines()[:2]
    assert "saved" in header
    assert "20" in first  # the year of an ISO timestamp
