"""The Postgres job queue: claims with SKIP LOCKED, leases on the database clock, and retries.

Needs a Postgres server. Set DA_AGENT_TEST_POSTGRES_URL, or the tests are skipped. Each test runs
in its own schema, which is dropped afterwards.
"""

import os
import threading
from uuid import UUID

import psycopg
import pytest

from da_agent.storage.db import connect, migrate
from da_agent.storage.jobs import JobQueue
from da_agent.storage.workspaces import create_workspace

URL = os.environ.get("DA_AGENT_TEST_POSTGRES_URL")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not URL, reason="DA_AGENT_TEST_POSTGRES_URL is not set"),
]


def expire_leases(conn: psycopg.Connection) -> None:
    """Moves time forward for the queue: every lease ran out a minute ago."""
    conn.execute("UPDATE jobs SET lease_until = now() - interval '1 minute'")


def test_migrations_apply_once_and_can_run_again(conn: psycopg.Connection) -> None:
    applied = migrate(conn)

    assert applied == []
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = current_schema()"
        )
    }
    assert {"schema_migrations", "workspaces", "jobs"} <= tables


def test_a_queued_job_is_claimed_once(conn: psycopg.Connection, workspace: UUID) -> None:
    queue = JobQueue(conn)
    job_id = queue.enqueue(workspace, "run", {"question": "Total?"})

    job = queue.claim("w1", lease_seconds=30)

    assert job is not None
    assert (job.id, job.status, job.attempts, job.payload) == (
        job_id,
        "running",
        1,
        {"question": "Total?"},
    )
    assert queue.claim("w2", lease_seconds=30) is None


def test_concurrent_workers_never_claim_the_same_job(
    schema: str, conn: psycopg.Connection, workspace: UUID
) -> None:
    queue = JobQueue(conn)
    for index in range(40):
        queue.enqueue(workspace, "run", {"n": index})
    claimed: list[UUID] = []
    lock = threading.Lock()

    def work(worker: str) -> None:
        assert URL is not None
        with connect(URL, schema=schema) as own:
            mine = JobQueue(own)
            while (job := mine.claim(worker, lease_seconds=30)) is not None:
                with lock:
                    claimed.append(job.id)

    threads = [threading.Thread(target=work, args=(f"w{i}",)) for i in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(claimed) == 40
    assert len(set(claimed)) == 40


def test_a_job_whose_lease_ran_out_is_claimed_again_and_the_old_worker_loses_it(
    conn: psycopg.Connection, workspace: UUID
) -> None:
    queue = JobQueue(conn)
    job_id = queue.enqueue(workspace, "run", {})
    queue.claim("dead", lease_seconds=30)
    expire_leases(conn)

    again = queue.claim("alive", lease_seconds=30)

    assert again is not None and (again.id, again.attempts) == (job_id, 2)
    assert not queue.complete(job_id, "dead", {"answer": 1})
    assert not queue.heartbeat(job_id, "dead", lease_seconds=30)
    assert queue.complete(job_id, "alive", {"answer": 2})
    done = queue.get(job_id, workspace)
    assert done is not None and (done.status, done.result) == ("succeeded", {"answer": 2})


def test_a_heartbeat_keeps_the_lease(conn: psycopg.Connection, workspace: UUID) -> None:
    queue = JobQueue(conn)
    job_id = queue.enqueue(workspace, "run", {})
    queue.claim("w1", lease_seconds=1)
    conn.execute("UPDATE jobs SET lease_until = now() + interval '1 second'")

    assert queue.heartbeat(job_id, "w1", lease_seconds=300)

    remaining = conn.execute(
        "SELECT lease_until - now() > interval '200 seconds' FROM jobs"
    ).fetchone()
    assert remaining == (True,)


def test_a_failed_job_waits_then_retries(conn: psycopg.Connection, workspace: UUID) -> None:
    queue = JobQueue(conn)
    job_id = queue.enqueue(workspace, "run", {})
    queue.claim("w1", lease_seconds=30)

    assert queue.fail(job_id, "w1", "provider timed out", retry_in_seconds=60)

    assert queue.claim("w1", lease_seconds=30) is None
    conn.execute("UPDATE jobs SET run_after = now() - interval '1 second'")
    again = queue.claim("w1", lease_seconds=30)
    assert again is not None and (again.attempts, again.last_error) == (2, "provider timed out")


def test_a_job_stops_retrying_after_its_last_attempt(
    conn: psycopg.Connection, workspace: UUID
) -> None:
    queue = JobQueue(conn)
    job_id = queue.enqueue(workspace, "run", {}, max_attempts=1)
    queue.claim("w1", lease_seconds=30)

    queue.fail(job_id, "w1", "boom", retry_in_seconds=0)

    job = queue.get(job_id, workspace)
    assert job is not None and (job.status, job.last_error) == ("failed", "boom")
    assert queue.claim("w1", lease_seconds=30) is None


def test_a_job_that_keeps_killing_its_worker_ends_failed(
    conn: psycopg.Connection, workspace: UUID
) -> None:
    queue = JobQueue(conn)
    job_id = queue.enqueue(workspace, "run", {}, max_attempts=2)
    for worker in ("w1", "w2"):
        assert queue.claim(worker, lease_seconds=30) is not None
        expire_leases(conn)

    assert queue.claim("w3", lease_seconds=30) is None
    assert queue.fail_expired() == 1
    job = queue.get(job_id, workspace)
    assert job is not None and job.status == "failed"
    assert "lease" in (job.last_error or "")


@pytest.mark.security
def test_a_workspace_cannot_read_another_workspaces_job(
    conn: psycopg.Connection, workspace: UUID
) -> None:
    queue = JobQueue(conn)
    other = create_workspace(conn, "other")
    job_id = queue.enqueue(workspace, "run", {"question": "secret"})

    assert queue.get(job_id, other) is None
    assert queue.get(job_id, workspace) is not None


def test_a_claim_skips_a_job_another_worker_is_claiming_instead_of_waiting(
    schema: str, conn: psycopg.Connection, workspace: UUID
) -> None:
    queue = JobQueue(conn)
    first = queue.enqueue(workspace, "run", {"n": 1})
    second = queue.enqueue(workspace, "run", {"n": 2})
    assert URL is not None
    with connect(URL, schema=schema) as other, other.transaction():
        # Another worker is in the middle of claiming the first job and holds its row lock.
        other.execute("SELECT id FROM jobs WHERE id = %s FOR UPDATE", (first,))
        conn.execute("SET statement_timeout = '2s'")

        job = queue.claim("w2", lease_seconds=30)

    assert job is not None and job.id == second
