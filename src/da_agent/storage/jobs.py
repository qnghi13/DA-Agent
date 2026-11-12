"""The job queue on Postgres (ADR 0003, ADR 0017).

A worker claims a job with `FOR UPDATE SKIP LOCKED`, so two workers never take the same one, and
holds it with a lease that it renews by heartbeat. Leases use the database clock, so worker clocks
do not matter. A worker that dies stops renewing; once its lease runs out another worker claims the
job again, and the dead worker's later writes are refused because they name the old worker. A job
that runs out of attempts ends failed instead of looping forever.

Every method is one SQL statement on an autocommit connection, so each is atomic on its own.
"""

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import UUID

import psycopg
from psycopg.rows import class_row
from psycopg.types.json import Jsonb

from da_agent.storage.db import returned_id

JobKind = Literal["ingest", "build", "run"]


@dataclass(frozen=True)
class Job:
    id: UUID
    workspace_id: UUID
    kind: str
    payload: dict[str, Any]
    status: str
    attempts: int
    max_attempts: int
    last_error: str | None
    result: dict[str, Any] | None
    trace_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RunSummary:
    id: UUID
    question: str
    as_of: str | None
    status: str
    created_at: dt.datetime


class JobQueue:
    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def _one(self, sql: str, params: tuple[Any, ...]) -> Job | None:
        with self._conn.cursor(row_factory=class_row(Job)) as cursor:
            return cursor.execute(sql, params).fetchone()  # type: ignore[arg-type]

    def enqueue(
        self,
        workspace_id: UUID,
        kind: JobKind,
        payload: dict[str, Any],
        *,
        max_attempts: int = 3,
    ) -> UUID:
        # A run in a conversation is also linked by column, so its turns are found by index.
        conversation = payload.get("conversation_id")
        cursor = self._conn.execute(
            "INSERT INTO jobs (workspace_id, kind, payload, max_attempts, conversation_id) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (
                workspace_id, kind, Jsonb(payload), max_attempts,
                UUID(str(conversation)) if conversation else None,
            ),
        )  # fmt: skip
        return returned_id(cursor)

    def claim(self, worker_id: str, *, lease_seconds: float) -> Job | None:
        """Take the oldest job that is due, or one whose worker's lease ran out."""
        return self._one(
            """
            UPDATE jobs SET status = 'running', attempts = attempts + 1, worker_id = %s,
                lease_until = now() + make_interval(secs => %s), updated_at = now(),
                started_at = coalesce(started_at, now())
            WHERE id = (
                SELECT id FROM jobs
                WHERE attempts < max_attempts AND (
                    (status = 'queued' AND run_after <= now())
                    OR (status = 'running' AND lease_until < now()))
                ORDER BY run_after
                FOR UPDATE SKIP LOCKED
                LIMIT 1)
            RETURNING id, workspace_id, kind, payload, status, attempts, max_attempts,
                last_error, result
            """,
            (worker_id, lease_seconds),
        )

    def heartbeat(self, job_id: UUID, worker_id: str, *, lease_seconds: float) -> bool:
        """Renew the lease. False means this worker lost the job and must stop working on it."""
        cursor = self._conn.execute(
            "UPDATE jobs SET lease_until = now() + make_interval(secs => %s), updated_at = now() "
            "WHERE id = %s AND worker_id = %s AND status = 'running' AND lease_until >= now()",
            (lease_seconds, job_id, worker_id),
        )
        return cursor.rowcount == 1

    def complete(self, job_id: UUID, worker_id: str, result: dict[str, Any]) -> bool:
        cursor = self._conn.execute(
            "UPDATE jobs SET status = 'succeeded', result = %s, lease_until = NULL, "
            "updated_at = now(), finished_at = now() "
            "WHERE id = %s AND worker_id = %s AND status = 'running'",
            (Jsonb(result), job_id, worker_id),
        )
        return cursor.rowcount == 1

    def fail(
        self, job_id: UUID, worker_id: str, error: str, *, retry_in_seconds: float | None
    ) -> bool:
        """Record a failure. With retry_in_seconds the job is queued again after that delay,
        unless it has used its last attempt."""
        cursor = self._conn.execute(
            """
            UPDATE jobs SET last_error = %s, lease_until = NULL, updated_at = now(),
                status = CASE WHEN %s::float8 IS NOT NULL AND attempts < max_attempts
                              THEN 'queued' ELSE 'failed' END,
                finished_at = CASE WHEN %s::float8 IS NOT NULL AND attempts < max_attempts
                                   THEN NULL ELSE now() END,
                run_after = now() + make_interval(secs => coalesce(%s::float8, 0))
            WHERE id = %s AND worker_id = %s AND status = 'running'
            """,
            (error, retry_in_seconds, retry_in_seconds, retry_in_seconds, job_id, worker_id),
        )
        return cursor.rowcount == 1

    def fail_expired(self) -> int:
        """End jobs whose lease ran out on their last attempt. Returns how many."""
        cursor = self._conn.execute(
            "UPDATE jobs SET status = 'failed', lease_until = NULL, updated_at = now(), "
            "finished_at = now(), "
            "last_error = 'the worker stopped renewing its lease on the last attempt' "
            "WHERE status = 'running' AND lease_until < now() AND attempts >= max_attempts"
        )
        return cursor.rowcount

    def get(self, job_id: UUID, workspace_id: UUID) -> Job | None:
        """A job, only if it belongs to the workspace."""
        return self._one(
            "SELECT id, workspace_id, kind, payload, status, attempts, max_attempts, last_error, "
            "result, trace_ids FROM jobs WHERE id = %s AND workspace_id = %s",
            (job_id, workspace_id),
        )

    def add_trace(self, job_id: UUID, worker_id: str, trace_id: str) -> bool:
        """Record the trace of the attempt this worker is running."""
        cursor = self._conn.execute(
            "UPDATE jobs SET trace_ids = array_append(trace_ids, %s) "
            "WHERE id = %s AND worker_id = %s AND status = 'running'",
            (trace_id, job_id, worker_id),
        )
        return cursor.rowcount == 1

    def runs_of_dataset(
        self,
        workspace_id: UUID,
        dataset_id: str,
        *,
        limit: int,
        before: dt.datetime | None = None,
    ) -> list[RunSummary]:
        """A dataset's runs in the workspace, newest first, older than `before` if given."""
        with self._conn.cursor(row_factory=class_row(RunSummary)) as cursor:
            return cursor.execute(
                "SELECT id, payload ->> 'question' AS question, payload ->> 'as_of' AS as_of, "
                "status, created_at FROM jobs "
                "WHERE workspace_id = %s AND kind = 'run' AND payload ->> 'dataset_id' = %s "
                "AND (%s::timestamptz IS NULL OR created_at < %s::timestamptz) "
                "ORDER BY created_at DESC, id DESC LIMIT %s",
                (workspace_id, dataset_id, before, before, limit),
            ).fetchall()
