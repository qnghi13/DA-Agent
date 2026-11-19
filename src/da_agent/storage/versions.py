"""Agent versions and the job attempts they ran (ADR 0038).

A worker registers its manifest once when it starts. Each claim of a job writes one attempt row,
keyed by the job and its attempt number, naming the worker, the agent version, and the trace. A
run resumed from a checkpoint that another version wrote is marked on its attempt.
"""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import class_row
from psycopg.types.json import Jsonb


@dataclass(frozen=True)
class Attempt:
    attempt: int
    worker_id: str
    agent_version: str | None
    trace_id: str | None
    resumed_from_version: str | None


def register_version(conn: psycopg.Connection, version: str, manifest: dict[str, Any]) -> None:
    """Record a manifest under its version id. The same id always has the same manifest."""
    conn.execute(
        "INSERT INTO agent_versions (id, manifest) VALUES (%s, %s) ON CONFLICT (id) DO NOTHING",
        (version, Jsonb(manifest)),
    )


def record_attempt(
    conn: psycopg.Connection,
    job_id: UUID,
    attempt: int,
    worker_id: str,
    agent_version: str | None,
    trace_id: str | None,
) -> None:
    """Record one claim of a job. A second record of the same attempt changes nothing."""
    conn.execute(
        "INSERT INTO job_attempts (job_id, attempt, worker_id, agent_version, trace_id) "
        "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (job_id, attempt) DO NOTHING",
        (job_id, attempt, worker_id, agent_version, trace_id),
    )


def mark_resumed_from(conn: psycopg.Connection, job_id: UUID, attempt: int, version: str) -> None:
    """Mark an attempt that continued a checkpoint another agent version wrote."""
    conn.execute(
        "UPDATE job_attempts SET resumed_from_version = %s WHERE job_id = %s AND attempt = %s",
        (version, job_id, attempt),
    )


def attempts_of(conn: psycopg.Connection, job_id: UUID) -> list[Attempt]:
    with conn.cursor(row_factory=class_row(Attempt)) as cursor:
        return cursor.execute(
            "SELECT attempt, worker_id, agent_version, trace_id, resumed_from_version "
            "FROM job_attempts WHERE job_id = %s ORDER BY attempt",
            (job_id,),
        ).fetchall()
