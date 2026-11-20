"""Feedback on runs, and the golden cases a thumbs-up makes (ADR 0043).

A member rates a finished run up or down. A thumbs-down can say why, with reasons from a fixed list
and an optional note. A thumbs-up on a run with an answer makes it a golden case: the question,
dataset, as-of date, and answer, kept so later code can be checked against it. Changing the rating
or removing it retires the case.
"""

import datetime as dt
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

import psycopg
from psycopg.rows import class_row, dict_row
from psycopg.types.json import Jsonb

from da_agent.storage.jobs import Job

Rating = Literal["up", "down"]
REASONS = ("wrong_number", "misunderstood", "should_abstain", "unclear", "other")
MAX_NOTE_CHARS = 1000


@dataclass(frozen=True)
class Feedback:
    rating: str
    reasons: list[str]
    note: str | None
    author: str
    updated_at: dt.datetime


@dataclass(frozen=True)
class GoldenCase:
    id: UUID
    dataset_id: UUID
    source_run_id: UUID
    question: str
    as_of: str
    expected: dict[str, Any]
    active: bool


def _golden_answer(job: Job) -> dict[str, Any] | None:
    """The answer a golden case expects, or None when the run gave nothing to check against."""
    result = job.result or {}
    answer = result.get("answer")
    if result.get("stop_reason") != "answered" or not isinstance(answer, dict):
        return None
    if answer.get("kind") == "unanswerable":
        return None
    return answer


def set_feedback(
    conn: psycopg.Connection,
    job: Job,
    rating: Rating,
    reasons: list[str],
    note: str | None,
    author: str,
) -> None:
    """Rate a finished run, replacing an earlier rating, and add or retire its golden case."""
    unknown = sorted(set(reasons) - set(REASONS))
    if unknown:
        raise ValueError(f"unknown reason {unknown[0]!r}; use one of {', '.join(REASONS)}")
    if rating == "up":
        kept_reasons: list[str] = []
        kept_note = None
    else:
        kept_reasons = [reason for reason in REASONS if reason in reasons]
        kept_note = (note or "").strip()[:MAX_NOTE_CHARS] or None
    with conn.transaction():
        conn.execute(
            """
            INSERT INTO run_feedback (job_id, workspace_id, rating, reasons, note, author)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (job_id) DO UPDATE SET rating = excluded.rating,
                reasons = excluded.reasons, note = excluded.note, author = excluded.author,
                updated_at = now()
            """,
            (job.id, job.workspace_id, rating, kept_reasons, kept_note, author),
        )
        expected = _golden_answer(job) if rating == "up" else None
        if expected is None:
            _retire(conn, job.id)
            return
        conn.execute(
            """
            INSERT INTO golden_cases
                (workspace_id, dataset_id, source_run_id, question, as_of, expected)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (source_run_id) DO UPDATE SET active = true, retired_at = NULL,
                expected = excluded.expected
            """,
            (
                job.workspace_id, UUID(str(job.payload["dataset_id"])), job.id,
                str(job.payload.get("question", "")), str(job.payload.get("as_of", "")),
                Jsonb(expected),
            ),
        )  # fmt: skip


def _retire(conn: psycopg.Connection, job_id: UUID) -> None:
    conn.execute(
        "UPDATE golden_cases SET active = false, retired_at = now() "
        "WHERE source_run_id = %s AND active",
        (job_id,),
    )


def clear_feedback(conn: psycopg.Connection, job_id: UUID, workspace_id: UUID) -> None:
    with conn.transaction():
        conn.execute(
            "DELETE FROM run_feedback WHERE job_id = %s AND workspace_id = %s",
            (job_id, workspace_id),
        )
        _retire(conn, job_id)


def feedback_of(conn: psycopg.Connection, job_id: UUID, workspace_id: UUID) -> Feedback | None:
    with conn.cursor(row_factory=class_row(Feedback)) as cursor:
        return cursor.execute(
            "SELECT rating, reasons, note, author, updated_at FROM run_feedback "
            "WHERE job_id = %s AND workspace_id = %s",
            (job_id, workspace_id),
        ).fetchone()


def golden_cases(
    conn: psycopg.Connection,
    workspace_id: UUID,
    dataset_id: UUID | None = None,
    *,
    active: bool = True,
) -> list[GoldenCase]:
    """A workspace's golden cases, oldest first: the active ones, or the retired ones."""
    with conn.cursor(row_factory=class_row(GoldenCase)) as cursor:
        return cursor.execute(
            "SELECT id, dataset_id, source_run_id, question, as_of, expected, active "
            "FROM golden_cases WHERE workspace_id = %s AND active = %s "
            "AND (%s::uuid IS NULL OR dataset_id = %s) ORDER BY created_at, id",
            (workspace_id, active, dataset_id, dataset_id),
        ).fetchall()


def disliked_runs(conn: psycopg.Connection, workspace_id: UUID, limit: int) -> list[dict[str, Any]]:
    """Runs rated down, newest first, with their reasons and notes: for an operator to review."""
    with conn.cursor(row_factory=dict_row) as cursor:
        return cursor.execute(
            "SELECT f.job_id AS run_id, j.payload ->> 'question' AS question, f.reasons, f.note, "
            "f.updated_at FROM run_feedback f JOIN jobs j ON j.id = f.job_id "
            "WHERE f.workspace_id = %s AND f.rating = 'down' ORDER BY f.updated_at DESC LIMIT %s",
            (workspace_id, limit),
        ).fetchall()
