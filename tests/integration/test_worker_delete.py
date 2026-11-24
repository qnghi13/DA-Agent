"""The worker's delete job: every row, file, and checkpoint of one dataset goes, and nothing else.

Needs DA_AGENT_TEST_POSTGRES_URL. See docs/design/2026-11-24-dataset-deletion.md.
"""

import dataclasses
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
import pytest
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.types.json import Jsonb

from da_agent.agent.checkpoints import checkpoint_connection
from da_agent.llm.client import CostRecord
from da_agent.storage import audit
from da_agent.storage.dataset_versions import DatasetVersionStore, ensure_first_version
from da_agent.storage.datasets import DatasetStore
from da_agent.storage.events import RunEvents
from da_agent.storage.exports import export_file
from da_agent.storage.feedback import set_feedback
from da_agent.storage.jobs import Job, JobQueue
from da_agent.storage.ledger import PostgresCostLedger
from da_agent.storage.memory import ConversationStore, MemoryStore
from da_agent.storage.versions import record_attempt
from da_agent.worker.deletion import DeleteHandler
from da_agent.worker.worker import JobWaitError, PermanentJobError, Worker, WorkerConfig

pytestmark = pytest.mark.integration
URL = os.environ.get("DA_AGENT_TEST_POSTGRES_URL", "")
ANSWERED = {"stop_reason": "answered", "answer": {"kind": "number", "value": 350}}
DAY = 86400


@dataclass
class Roots:
    uploads: Path
    datasets: Path
    exports: Path
    staging: Path


@dataclass
class Shop:
    """One dataset with every kind of row, file, and checkpoint thread it can own."""

    id: UUID
    run: UUID
    jobs: list[UUID]
    paths: list[Path]
    threads: list[str]


@dataclass
class Setup:
    workspace: UUID
    schema: str
    roots: Roots
    doomed: Shop
    other: Shop
    stale_staging: Path
    fresh_staging: Path


def _write(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "data.bin").write_text("rows", encoding="utf-8")
    return path


def _put_thread(schema: str, thread_id: str) -> None:
    with checkpoint_connection(URL, schema) as cp:
        saver = PostgresSaver(cp)
        saver.setup()
        checkpoint = empty_checkpoint()
        # A list, not a string: only non-primitive values are stored as blobs.
        checkpoint["channel_values"] = {"question": ["Total revenue?"]}
        checkpoint["channel_versions"] = {"question": 1}
        config: Any = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
        saved = saver.put(config, checkpoint, {}, {"question": 1})
        saver.put_writes(saved, [("question", "Total revenue?")], "task-1")


def _make_shop(
    conn: psycopg.Connection, workspace: UUID, schema: str, roots: Roots, name: str
) -> Shop:
    dataset = DatasetStore(conn).create(workspace, name, "")
    conn.execute("UPDATE datasets SET status = 'ready' WHERE id = %s", (dataset,))
    ensure_first_version(conn, dataset)
    versions = DatasetVersionStore(conn)
    upload_v2 = f"{dataset}-v2-ab12"
    version = versions.open_version(workspace, dataset, [], upload_v2, 9)
    assert versions.apply(dataset, version, f"{dataset}-v2", expected=("uploaded",))
    paths = [
        _write(roots.uploads / str(dataset)),
        _write(roots.uploads / upload_v2),
        _write(roots.datasets / str(dataset)),
        _write(roots.datasets / f"{dataset}-v2"),
        _write(roots.datasets / f"{dataset}.old"),
    ]
    conversation = ConversationStore(conn).create(workspace, dataset)
    queue = JobQueue(conn)
    payload = {
        "dataset_id": str(dataset), "question": "Total revenue?", "as_of": "2026-06-28",
        "conversation_id": str(conversation),
    }  # fmt: skip
    run = queue.enqueue(workspace, "run", payload)
    query = queue.enqueue(workspace, "query", {"dataset_id": str(dataset), "run_id": str(run)})
    export = queue.enqueue(workspace, "export", {"dataset_id": str(dataset), "run_id": str(run)})
    ingest = queue.enqueue(workspace, "ingest", {"dataset_id": str(dataset)})
    # Opening version 2 queued a version job.
    version_jobs = conn.execute(
        "SELECT id FROM jobs WHERE kind = 'version' AND payload ->> 'dataset_id' = %s",
        (str(dataset),),
    ).fetchall()
    jobs = [run, query, export, ingest, *(row[0] for row in version_jobs)]
    conn.execute(
        "UPDATE jobs SET status = 'succeeded', result = %s WHERE id = ANY(%s)",
        (Jsonb(ANSWERED), jobs),
    )
    RunEvents(conn).append(run, workspace, {"type": "step", "node": "plan"})
    record_attempt(conn, run, 1, "w0", None, None)
    found = queue.get(run, workspace)
    assert found is not None
    set_feedback(conn, found, "up", [], None, "me")
    memory = MemoryStore(conn)
    memory.write(workspace, dataset, "correction", "Revenue excludes refunds.", author="me")
    memory.save_metric(
        workspace, dataset, name="revenue", aliases=[], definition={"sql": "SELECT 1"},
        text="Revenue is the sum of totals.", author="me", source_run_id=run,
    )  # fmt: skip
    csv = export_file(roots.exports, export)
    csv.parent.mkdir(parents=True, exist_ok=True)
    csv.write_text("id\n1\n", encoding="utf-8")
    paths.append(csv)
    threads = [str(run), f"fork-{run}-1a2b"]
    for thread in threads:
        _put_thread(schema, thread)
    PostgresCostLedger(conn).record(
        CostRecord(str(run), "groq", "m", 10, 5, 0, 0.001, 0.2)
    )  # fmt: skip
    audit.record(conn, "run.create", workspace_id=workspace, target=str(run))
    return Shop(dataset, run, jobs, paths, threads)


@pytest.fixture
def full_dataset(conn: psycopg.Connection, workspace: UUID, schema: str, tmp_path: Path) -> Setup:
    roots = Roots(
        tmp_path / "uploads", tmp_path / "datasets", tmp_path / "exports", tmp_path / "staging"
    )
    doomed = _make_shop(conn, workspace, schema, roots, "doomed")
    other = _make_shop(conn, workspace, schema, roots, "other")
    stale = _write(roots.staging / "da-sbx-stale")
    old = time.time() - 2 * DAY
    os.utime(stale, (old, old))
    fresh = _write(roots.staging / "da-sbx-fresh")
    assert DatasetStore(conn).start_delete(workspace, doomed.id) is not None
    return Setup(workspace, schema, roots, doomed, other, stale, fresh)


def handler(setup: Setup, **overrides: Any) -> DeleteHandler:
    roots = setup.roots
    settings: dict[str, Any] = {
        "url": URL, "schema": setup.schema, "uploads_root": roots.uploads,
        "datasets_root": roots.datasets, "exports_root": roots.exports,
        "staging_root": roots.staging,
    }  # fmt: skip
    return DeleteHandler(**(settings | overrides))


def claim_delete(conn: psycopg.Connection) -> Job:
    job = JobQueue(conn).claim("w1", lease_seconds=60)
    assert job is not None and job.kind == "delete"
    return job


def delete_job_id(conn: psycopg.Connection, dataset: UUID) -> UUID:
    row = conn.execute(
        "SELECT id FROM jobs WHERE kind = 'delete' AND payload ->> 'dataset_id' = %s",
        (str(dataset),),
    ).fetchone()
    assert row is not None
    return row[0]


def count(conn: psycopg.Connection, sql: str, *params: Any) -> int:
    row = conn.execute(sql, params).fetchone()  # type: ignore[arg-type]
    assert row is not None
    return int(row[0])


def rows_of(conn: psycopg.Connection, shop: Shop) -> dict[str, int]:
    """How many rows the shop has in each table that holds its data."""
    d, jobs, threads = str(shop.id), shop.jobs, shop.threads
    return {
        "datasets": count(conn, "SELECT count(*) FROM datasets WHERE id::text = %s", d),
        "versions": count(
            conn, "SELECT count(*) FROM dataset_versions WHERE dataset_id::text = %s", d
        ),
        "memories": count(
            conn, "SELECT count(*) FROM dataset_memories WHERE dataset_id::text = %s", d
        ),
        "conversations": count(
            conn, "SELECT count(*) FROM conversations WHERE dataset_id::text = %s", d
        ),
        "jobs": count(conn, "SELECT count(*) FROM jobs WHERE id = ANY(%s)", jobs),
        "run_events": count(conn, "SELECT count(*) FROM run_events WHERE job_id = ANY(%s)", jobs),
        "attempts": count(conn, "SELECT count(*) FROM job_attempts WHERE job_id = ANY(%s)", jobs),
        "feedback": count(conn, "SELECT count(*) FROM run_feedback WHERE job_id = ANY(%s)", jobs),
        "golden": count(conn, "SELECT count(*) FROM golden_cases WHERE dataset_id::text = %s", d),
        "checkpoints": count(
            conn, "SELECT count(*) FROM checkpoints WHERE thread_id = ANY(%s)", threads
        ),
        "blobs": count(
            conn, "SELECT count(*) FROM checkpoint_blobs WHERE thread_id = ANY(%s)", threads
        ),
        "writes": count(
            conn, "SELECT count(*) FROM checkpoint_writes WHERE thread_id = ANY(%s)", threads
        ),
    }


def assert_gone(conn: psycopg.Connection, setup: Setup) -> None:
    doomed = setup.doomed
    assert rows_of(conn, doomed) == dict.fromkeys(rows_of(conn, doomed), 0)
    assert [path for path in doomed.paths if path.exists()] == []
    assert not setup.stale_staging.exists()


def assert_untouched(conn: psycopg.Connection, setup: Setup, before: dict[str, int]) -> None:
    assert rows_of(conn, setup.other) == before
    assert all(path.exists() for path in setup.other.paths)
    assert setup.fresh_staging.exists()
    # Kept on purpose: costs, the audit trail, and the delete job's own row.
    assert count(conn, "SELECT count(*) FROM llm_calls") == 2
    assert count(conn, "SELECT count(*) FROM audit_log WHERE action = 'run.create'") == 2
    status = conn.execute(
        "SELECT status FROM jobs WHERE id = %s", (delete_job_id(conn, setup.doomed.id),)
    ).fetchone()
    assert status is not None


def test_a_delete_removes_the_whole_inventory_and_nothing_else(
    conn: psycopg.Connection, full_dataset: Setup
) -> None:
    before = rows_of(conn, full_dataset.other)
    assert all(before.values())
    assert all(rows_of(conn, full_dataset.doomed).values())

    result = handler(full_dataset)(claim_delete(conn), threading.Event())

    assert result == {"status": "deleted"}
    assert_gone(conn, full_dataset)
    assert_untouched(conn, full_dataset, before)


def test_a_failed_earlier_delete_job_of_the_dataset_is_removed_too(
    conn: psycopg.Connection, full_dataset: Setup
) -> None:
    doomed = full_dataset.doomed
    first = delete_job_id(conn, doomed.id)
    conn.execute("UPDATE jobs SET status = 'failed' WHERE id = %s", (first,))
    second = DatasetStore(conn).start_delete(full_dataset.workspace, doomed.id)
    assert second is not None and second != first

    handler(full_dataset)(claim_delete(conn), threading.Event())

    kept = conn.execute(
        "SELECT id FROM jobs WHERE payload ->> 'dataset_id' = %s", (str(doomed.id),)
    ).fetchall()
    assert [row[0] for row in kept] == [second]


def _start_running_run(conn: psycopg.Connection, setup: Setup) -> UUID:
    """A run of the doomed dataset that a worker holds, as if it was claimed before the delete."""
    run = JobQueue(conn).enqueue(
        setup.workspace, "run", {"dataset_id": str(setup.doomed.id), "question": "q"}
    )
    conn.execute(
        "UPDATE jobs SET status = 'running', attempts = 1, worker_id = 'w0', "
        "lease_until = now() + interval '1 hour' WHERE id = %s",
        (run,),
    )
    setup.doomed.jobs.append(run)
    return run


def test_a_running_run_blocks_the_delete_until_it_ends(
    conn: psycopg.Connection, full_dataset: Setup
) -> None:
    run = _start_running_run(conn, full_dataset)
    delete = handler(full_dataset, wait_seconds=0)
    job = claim_delete(conn)
    with pytest.raises(JobWaitError):
        delete(dataclasses.replace(job, queue_wait_seconds=None), threading.Event())
    # Through the worker: the job waits without spending its attempt.
    conn.execute("UPDATE jobs SET status = 'queued', attempts = 0 WHERE id = %s", (job.id,))
    worker = Worker(WorkerConfig(URL, "w1", full_dataset.schema), {"delete": delete})
    assert worker.run_once()

    waiting = conn.execute(
        "SELECT status, attempts, cancel_requested_at IS NOT NULL FROM jobs WHERE id = %s",
        (job.id,),
    ).fetchone()
    assert waiting is not None and waiting[:2] == ("queued", 0)
    asked = conn.execute(
        "SELECT cancel_requested_at IS NOT NULL FROM jobs WHERE id = %s", (run,)
    ).fetchone()
    assert asked == (True,)
    assert all(path.exists() for path in full_dataset.doomed.paths)
    assert all(rows_of(conn, full_dataset.doomed).values())

    conn.execute("UPDATE jobs SET status = 'cancelled', lease_until = NULL WHERE id = %s", (run,))
    assert worker.run_once()

    done = conn.execute("SELECT status, result FROM jobs WHERE id = %s", (job.id,)).fetchone()
    assert done == ("succeeded", {"status": "deleted"})
    assert_gone(conn, full_dataset)


def test_a_build_installed_during_the_wait_is_removed(
    conn: psycopg.Connection, full_dataset: Setup
) -> None:
    run = _start_running_run(conn, full_dataset)
    delete = handler(full_dataset)
    job = claim_delete(conn)
    with pytest.raises(JobWaitError):
        delete(job, threading.Event())
    late = _write(full_dataset.roots.datasets / f"{full_dataset.doomed.id}-v3")
    late_old = _write(full_dataset.roots.datasets / f"{full_dataset.doomed.id}-v3.old")
    conn.execute("UPDATE jobs SET status = 'succeeded' WHERE id = %s", (run,))

    assert delete(job, threading.Event()) == {"status": "deleted"}

    assert not late.exists() and not late_old.exists()
    assert_gone(conn, full_dataset)


def test_a_delete_that_waited_too_long_fails(conn: psycopg.Connection, full_dataset: Setup) -> None:
    _start_running_run(conn, full_dataset)
    job = claim_delete(conn)
    old = dataclasses.replace(job, queue_wait_seconds=7201.0)

    with pytest.raises(PermanentJobError, match="did not stop"):
        handler(full_dataset)(old, threading.Event())

    assert all(path.exists() for path in full_dataset.doomed.paths)


@pytest.mark.parametrize("crash_after", ["files", "checkpoints"])
def test_a_delete_finishes_after_a_crash_at_any_step(
    conn: psycopg.Connection, full_dataset: Setup, monkeypatch: pytest.MonkeyPatch,
    crash_after: str,
) -> None:  # fmt: skip
    before = rows_of(conn, full_dataset.other)
    delete = handler(full_dataset)
    job = claim_delete(conn)
    next_step = {"files": "_delete_checkpoints", "checkpoints": "_delete_rows"}[crash_after]

    def crash(*args: Any) -> None:
        raise RuntimeError("worker killed")

    with monkeypatch.context() as patch:
        patch.setattr(delete, next_step, crash)
        with pytest.raises(RuntimeError):
            delete(job, threading.Event())
    assert count(conn, "SELECT count(*) FROM datasets WHERE id = %s", full_dataset.doomed.id)

    assert delete(job, threading.Event()) == {"status": "deleted"}

    assert_gone(conn, full_dataset)
    assert_untouched(conn, full_dataset, before)


def test_a_delete_of_a_dataset_already_gone_succeeds(
    conn: psycopg.Connection, full_dataset: Setup
) -> None:
    delete = handler(full_dataset)
    job = claim_delete(conn)
    assert delete(job, threading.Event()) == {"status": "deleted"}

    assert delete(job, threading.Event()) == {"status": "deleted"}


def test_a_delete_job_never_touches_a_dataset_of_another_workspace(
    conn: psycopg.Connection, full_dataset: Setup
) -> None:
    before = rows_of(conn, full_dataset.other)
    job = claim_delete(conn)
    foreign = dataclasses.replace(job, payload={"dataset_id": str(full_dataset.other.id)})
    stranger = dataclasses.replace(foreign, workspace_id=UUID(int=1))

    assert handler(full_dataset)(stranger, threading.Event()) == {"status": "deleted"}

    assert_untouched(conn, full_dataset, before)


def test_a_delete_job_refuses_a_dataset_that_is_not_being_deleted(
    conn: psycopg.Connection, full_dataset: Setup
) -> None:
    before = rows_of(conn, full_dataset.other)
    job = claim_delete(conn)
    live = dataclasses.replace(job, payload={"dataset_id": str(full_dataset.other.id)})

    with pytest.raises(PermanentJobError, match="not being deleted"):
        handler(full_dataset)(live, threading.Event())

    assert_untouched(conn, full_dataset, before)


@pytest.mark.security
@pytest.mark.parametrize(
    ("column", "value"),
    [("build_dir", "../x"), ("upload_dir", ".."), ("build_dir", "a/../../x"), ("build_dir", "abs")],
)
def test_a_build_dir_outside_its_root_is_never_removed(
    conn: psycopg.Connection, full_dataset: Setup, tmp_path: Path, column: str, value: str
) -> None:
    outside = _write(tmp_path / "x")
    if value == "abs":
        value = str(outside)
    conn.execute(
        f"UPDATE dataset_versions SET {column} = %s WHERE dataset_id = %s AND version = 2",  # noqa: S608
        (value, full_dataset.doomed.id),
    )

    handler(full_dataset)(claim_delete(conn), threading.Event())

    assert (outside / "data.bin").exists()
    assert full_dataset.roots.uploads.is_dir() and full_dataset.roots.datasets.is_dir()
    assert all(path.exists() for path in full_dataset.other.paths)
