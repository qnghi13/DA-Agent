"""Agent versions and job attempts in Postgres (ADR 0038).

Needs a Postgres server. Set DA_AGENT_TEST_POSTGRES_URL, or the tests are skipped.
"""

from uuid import UUID

import psycopg
import pytest

from da_agent.storage.jobs import JobQueue
from da_agent.storage.versions import (
    Attempt,
    attempts_of,
    mark_resumed_from,
    record_attempt,
    register_version,
)

pytestmark = pytest.mark.integration


def test_registering_a_version_twice_keeps_one_row(conn: psycopg.Connection) -> None:
    register_version(conn, "abc123def456", {"code": {"sha": "x"}})
    register_version(conn, "abc123def456", {"code": {"sha": "x"}})

    rows = conn.execute("SELECT id, manifest FROM agent_versions").fetchall()
    assert rows == [("abc123def456", {"code": {"sha": "x"}})]


def test_attempts_are_listed_in_order_with_their_version(
    conn: psycopg.Connection, workspace: UUID
) -> None:
    job_id = JobQueue(conn).enqueue(workspace, "run", {})
    register_version(conn, "v1", {})

    record_attempt(conn, job_id, 2, "w2", None, None)
    record_attempt(conn, job_id, 1, "w1", "v1", "trace-a")

    assert attempts_of(conn, job_id) == [
        Attempt(1, "w1", "v1", "trace-a", None),
        Attempt(2, "w2", None, None, None),
    ]


def test_recording_the_same_attempt_again_changes_nothing(
    conn: psycopg.Connection, workspace: UUID
) -> None:
    job_id = JobQueue(conn).enqueue(workspace, "run", {})

    record_attempt(conn, job_id, 1, "w1", None, "t1")
    record_attempt(conn, job_id, 1, "w9", None, "t9")

    assert attempts_of(conn, job_id) == [Attempt(1, "w1", None, "t1", None)]


def test_a_resume_mark_lands_on_its_attempt(conn: psycopg.Connection, workspace: UUID) -> None:
    job_id = JobQueue(conn).enqueue(workspace, "run", {})
    record_attempt(conn, job_id, 1, "w1", None, None)
    record_attempt(conn, job_id, 2, "w2", None, None)

    # An old checkpoint may name a version this database never registered.
    mark_resumed_from(conn, job_id, 2, "never-registered")

    assert [a.resumed_from_version for a in attempts_of(conn, job_id)] == [
        None,
        "never-registered",
    ]


def test_deleting_a_job_deletes_its_attempts(conn: psycopg.Connection, workspace: UUID) -> None:
    job_id = JobQueue(conn).enqueue(workspace, "run", {})
    record_attempt(conn, job_id, 1, "w1", None, None)

    conn.execute("DELETE FROM jobs WHERE id = %s", (job_id,))

    assert attempts_of(conn, job_id) == []


def test_an_attempt_cannot_name_an_unregistered_version(
    conn: psycopg.Connection, workspace: UUID
) -> None:
    job_id = JobQueue(conn).enqueue(workspace, "run", {})

    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        record_attempt(conn, job_id, 1, "w1", "nope", None)
