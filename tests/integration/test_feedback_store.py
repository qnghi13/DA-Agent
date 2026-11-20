"""Feedback on runs and the golden cases a 👍 makes (ADR 0043).

Needs a Postgres server. Set DA_AGENT_TEST_POSTGRES_URL, or the tests are skipped.
"""

from typing import Any
from uuid import UUID

import psycopg
import pytest

from da_agent.storage.feedback import (
    clear_feedback,
    disliked_runs,
    feedback_of,
    golden_cases,
    set_feedback,
)
from da_agent.storage.jobs import Job, JobQueue
from da_agent.storage.workspaces import create_workspace

pytestmark = pytest.mark.integration


def answered(kind: str = "number", value: Any = 350) -> dict[str, Any]:
    return {"stop_reason": "answered", "answer": {"kind": kind, "value": value, "reason": None}}


def finished_run(
    conn: psycopg.Connection, workspace: UUID, result: dict[str, Any] | None = None
) -> Job:
    queue = JobQueue(conn)
    payload = {"question": "Total?", "as_of": "2026-06-28", "dataset_id": str(UUID(int=7))}
    job_id = queue.enqueue(workspace, "run", payload)
    claimed = queue.claim("w", lease_seconds=30)
    assert claimed is not None and claimed.id == job_id
    queue.complete(job_id, "w", answered() if result is None else result)
    found = queue.get(job_id, workspace)
    assert found is not None
    return found


def test_a_thumbs_up_on_an_answer_makes_a_golden_case(
    conn: psycopg.Connection, workspace: UUID
) -> None:
    run = finished_run(conn, workspace)

    set_feedback(conn, run, "up", [], None, "member")

    feedback = feedback_of(conn, run.id, workspace)
    assert feedback is not None and (feedback.rating, feedback.reasons) == ("up", [])
    (case,) = golden_cases(conn, workspace)
    assert (case.source_run_id, case.question, case.as_of) == (run.id, "Total?", "2026-06-28")
    assert case.expected == {"kind": "number", "value": 350, "reason": None}
    assert case.dataset_id == UUID(int=7)


def test_rating_again_keeps_one_row_and_a_thumbs_down_retires_the_case(
    conn: psycopg.Connection, workspace: UUID
) -> None:
    run = finished_run(conn, workspace)
    set_feedback(conn, run, "up", [], None, "member")
    set_feedback(conn, run, "up", [], None, "member")

    set_feedback(conn, run, "down", ["wrong_number", "unclear"], "  June is missing  ", "member")

    feedback = feedback_of(conn, run.id, workspace)
    assert feedback is not None
    assert (feedback.rating, feedback.reasons, feedback.note) == (
        "down", ["wrong_number", "unclear"], "June is missing",
    )  # fmt: skip
    assert conn.execute("SELECT count(*) FROM run_feedback").fetchone() == (1,)
    assert golden_cases(conn, workspace) == []
    (retired,) = golden_cases(conn, workspace, active=False)
    assert not retired.active

    set_feedback(conn, run, "up", [], None, "member")
    assert len(golden_cases(conn, workspace)) == 1
    assert conn.execute("SELECT count(*) FROM golden_cases").fetchone() == (1,)


def test_removing_the_rating_retires_the_case(conn: psycopg.Connection, workspace: UUID) -> None:
    run = finished_run(conn, workspace)
    set_feedback(conn, run, "up", [], None, "member")

    clear_feedback(conn, run.id, workspace)

    assert feedback_of(conn, run.id, workspace) is None
    assert golden_cases(conn, workspace) == []


@pytest.mark.parametrize(
    "result",
    [
        {"stop_reason": "budget_exceeded", "answer": None},
        {"stop_reason": "answered", "answer": {"kind": "unanswerable", "value": None}},
    ],
)
def test_a_thumbs_up_without_an_answer_makes_no_case(
    conn: psycopg.Connection, workspace: UUID, result: dict[str, Any]
) -> None:
    run = finished_run(conn, workspace, result)

    set_feedback(conn, run, "up", [], None, "member")

    assert feedback_of(conn, run.id, workspace) is not None
    assert golden_cases(conn, workspace) == []


def test_reasons_and_notes_stay_only_with_a_thumbs_down(
    conn: psycopg.Connection, workspace: UUID
) -> None:
    run = finished_run(conn, workspace)

    set_feedback(conn, run, "up", ["unclear"], "great", "member")

    feedback = feedback_of(conn, run.id, workspace)
    assert feedback is not None and (feedback.reasons, feedback.note) == ([], None)


def test_an_unknown_reason_or_a_blank_note_is_handled(
    conn: psycopg.Connection, workspace: UUID
) -> None:
    run = finished_run(conn, workspace)

    with pytest.raises(ValueError, match="reason"):
        set_feedback(conn, run, "down", ["rude"], None, "member")
    set_feedback(conn, run, "down", ["other", "other"], "   ", "member")

    feedback = feedback_of(conn, run.id, workspace)
    assert feedback is not None and (feedback.reasons, feedback.note) == (["other"], None)


def test_feedback_stays_in_its_workspace(conn: psycopg.Connection, workspace: UUID) -> None:
    run = finished_run(conn, workspace)
    set_feedback(conn, run, "down", ["misunderstood"], "not this", "member")
    other = create_workspace(conn, "other")

    assert feedback_of(conn, run.id, other) is None
    assert golden_cases(conn, other) == []
    assert disliked_runs(conn, other, limit=10) == []
    (row,) = disliked_runs(conn, workspace, limit=10)
    assert (row["question"], row["reasons"], row["note"]) == (
        "Total?",
        ["misunderstood"],
        "not this",
    )
