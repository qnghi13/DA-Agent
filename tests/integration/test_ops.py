"""Operator dashboard aggregates over fixed rows (ADR 0026). Needs Postgres."""

from typing import Any
from uuid import UUID

import psycopg
import pytest
from psycopg.types.json import Jsonb

from da_agent.llm.client import CostRecord
from da_agent.storage import audit
from da_agent.storage.events import RunEvents
from da_agent.storage.jobs import JobQueue
from da_agent.storage.ledger import PostgresCostLedger
from da_agent.storage.ops import failures, summary
from da_agent.storage.workspaces import create_workspace, set_daily_budget

pytestmark = pytest.mark.integration


def answered(kind: str = "number") -> dict[str, Any]:
    return {"answer": {"kind": kind, "value": 1}, "stop_reason": "answered", "error": None}


def stopped(reason: str, error: str = "the numbers 42 do not appear") -> dict[str, Any]:
    return {"answer": None, "stop_reason": reason, "error": error}


def job(
    conn: psycopg.Connection,
    workspace: UUID,
    *,
    status: str,
    result: dict[str, Any] | None = None,
    kind: str = "run",
    wait: int = 2,
    took: int = 10,
    attempts: int = 1,
    error: str | None = None,
    age: str = "1 hour",
) -> UUID:
    job_id = JobQueue(conn).enqueue(workspace, kind, {"question": "secret question"})  # type: ignore[arg-type]
    finished = status in ("succeeded", "failed")
    conn.execute(
        "UPDATE jobs SET status = %s, result = %s, attempts = %s, last_error = %s, "
        "created_at = now() - %s::interval, "
        "started_at = CASE WHEN %s = 'queued' THEN NULL "
        "  ELSE now() - %s::interval + make_interval(secs => %s) END, "
        "finished_at = CASE WHEN %s "
        "  THEN now() - %s::interval + make_interval(secs => %s + %s) END "
        "WHERE id = %s",
        (
            status, Jsonb(result) if result else None, attempts, error, age,
            status, age, wait, finished, age, wait, took, job_id,
        ),
    )  # fmt: skip
    return job_id


def call(
    job_id: UUID, model: str, cost: float, latency: float, error: str | None = None
) -> CostRecord:
    return CostRecord(
        run_id=str(job_id), provider="groq", model=model, input_tokens=0 if error else 1000,
        output_tokens=0 if error else 100, cached_input_tokens=0 if error else 500,
        cost_usd=cost, latency_seconds=latency, error=error,
    )  # fmt: skip


@pytest.fixture
def shop(conn: psycopg.Connection) -> UUID:
    workspace = create_workspace(conn, "Coffee shop")
    set_daily_budget(conn, workspace, 2.0)
    return workspace


def test_a_fresh_install_has_empty_panels(conn: psycopg.Connection) -> None:
    data = summary(conn, "24h")

    assert data["window"] == "24h" and data["bucket"] == "hour"
    assert data["now"]["queued"] == 0 and data["now"]["oldest_queued_seconds"] is None
    assert data["runs"]["total"] == 0 and data["runs"]["answer_rate"] is None
    assert data["latency"]["run_p50"] is None
    assert data["models"] == [] and data["model_errors"]["total"] == 0
    assert data["cost"]["total_usd"] == 0 and data["cost"]["per_answered_run_usd"] is None
    assert failures(conn, limit=10) == []


def test_run_outcomes_are_classified(conn: psycopg.Connection, shop: UUID) -> None:
    for _ in range(3):
        job(conn, shop, status="succeeded", result=answered())
    job(conn, shop, status="succeeded", result=answered("unanswerable"))
    job(conn, shop, status="succeeded", result=stopped("unverified"))
    job(conn, shop, status="succeeded", result=stopped("unreadable_values"))
    job(conn, shop, status="succeeded", result=stopped("llm_error"))
    job(conn, shop, status="succeeded", result=stopped("budget_exceeded"))
    job(conn, shop, status="failed", error="boom", attempts=3)
    job(conn, shop, status="running")
    job(conn, shop, status="succeeded", result=answered(), age="3 days")
    job(conn, shop, status="succeeded", kind="ingest", result={})

    runs = summary(conn, "24h")["runs"]

    assert runs["total"] == 10
    assert runs["outcomes"] == {
        "answered": 3,
        "abstained": 1,
        "unverified": 2,
        "model_error": 1,
        "budget_exceeded": 1,
        "failed": 1,
        "in_progress": 1,
    }
    # Rates are over finished runs only.
    assert runs["answer_rate"] == pytest.approx(3 / 9)
    assert runs["abstain_rate"] == pytest.approx(1 / 9)
    assert runs["retried"] == 1
    assert sum(point["count"] for point in runs["series"]) == 10
    assert summary(conn, "7d")["runs"]["total"] == 11


def test_latency_splits_queue_wait_from_run_time(conn: psycopg.Connection, shop: UUID) -> None:
    for took in (10, 20, 30, 40, 100):
        job(conn, shop, status="succeeded", result=answered(), wait=4, took=took)
    job(conn, shop, status="queued", age="5 minutes")

    data = summary(conn, "24h")

    assert data["latency"]["queue_wait_p50"] == pytest.approx(4, abs=0.01)
    assert data["latency"]["run_p50"] == pytest.approx(30, abs=0.01)
    assert data["latency"]["run_p95"] > 80
    assert data["now"]["queued"] == 1
    assert data["now"]["oldest_queued_seconds"] == pytest.approx(300, abs=5)


def test_model_calls_cost_and_errors(conn: psycopg.Connection, shop: UUID) -> None:
    other = create_workspace(conn, "Bakery")
    first = job(conn, shop, status="succeeded", result=answered())
    second = job(conn, shop, status="succeeded", result=answered())
    third = job(conn, other, status="succeeded", result=stopped("llm_error"))
    ledger = PostgresCostLedger(conn)
    ledger.record(call(first, "gpt-oss-20b", 0.01, 1.0))
    ledger.record(call(first, "gpt-oss-20b", 0.0, 0.2, error="rate_limited"))
    ledger.record(call(second, "gpt-oss-20b", 0.03, 3.0))
    ledger.record(call(third, "llama", 0.005, 0.5))
    ledger.record(call(third, "llama", 0.0, 9.0, error="transient"))

    data = summary(conn, "24h")

    models = {m["model"]: m for m in data["models"]}
    assert models["gpt-oss-20b"]["calls"] == 2 and models["gpt-oss-20b"]["errors"] == 1
    assert models["gpt-oss-20b"]["cached_share"] == pytest.approx(0.5)
    assert models["gpt-oss-20b"]["cost_usd"] == pytest.approx(0.04)
    assert models["llama"]["errors"] == 1
    assert data["model_errors"]["total"] == 2
    assert data["model_errors"]["by_kind"] == {"rate_limited": 1, "transient": 1}
    assert data["cost"]["total_usd"] == pytest.approx(0.045)
    assert data["cost"]["per_answered_run_usd"] == pytest.approx(0.02)
    by_workspace = [(w["name"], round(w["cost_usd"], 3)) for w in data["cost"]["by_workspace"]]
    assert by_workspace == [("Coffee shop", 0.04), ("Bakery", 0.005)]
    today = {w["name"]: w for w in data["cost"]["today"]}
    assert today["Coffee shop"]["budget_usd"] == 2.0
    assert today["Bakery"]["budget_usd"] is None  # the default applies; the API fills it in


def test_quality_and_ingestion_signals(conn: psycopg.Connection, shop: UUID) -> None:
    run = job(conn, shop, status="succeeded", result=answered())
    events = RunEvents(conn)
    events.append(run, shop, {"type": "verify", "passed": False, "problems": ["the numbers 42"]})
    events.append(run, shop, {"type": "verify", "passed": True, "problems": []})
    events.append(run, shop, {"type": "tool", "tool": "run_sql", "ok": False, "error": "no x"})
    events.append(run, shop, {"type": "tool", "tool": "run_sql", "ok": True, "error": None})
    job(conn, shop, status="failed", kind="ingest", error="bad file")
    job(conn, shop, status="succeeded", kind="ingest", result={})

    quality = summary(conn, "24h")["quality"]

    assert quality["verify_checks"] == 2 and quality["verify_failed"] == 1
    assert quality["runs_repaired"] == 1
    assert (quality["tool_calls"], quality["tool_errors"]) == (2, 1)
    assert (quality["ingest_jobs"], quality["ingest_failed"]) == (2, 1)
    assert "the numbers" not in str(quality)


def test_security_counts_failed_sign_ins(conn: psycopg.Connection, shop: UUID) -> None:
    for _ in range(3):
        audit.record(conn, "auth.failed", detail={"path": "/v1/datasets"})
    audit.record(conn, "auth.sign_in", workspace_id=shop, detail={"role": "member"})

    security = summary(conn, "24h")["security"]

    assert (security["auth_failed"], security["sign_ins"]) == (3, 1)


def test_now_shows_stuck_work_and_datasets_waiting(conn: psycopg.Connection, shop: UUID) -> None:
    stuck = job(conn, shop, status="running")
    conn.execute(
        "UPDATE jobs SET lease_until = now() - interval '1 minute', updated_at = now() "
        "WHERE id = %s",
        (stuck,),
    )
    conn.execute(
        "INSERT INTO datasets (workspace_id, name, status) VALUES (%s, 'a', 'proposed')", (shop,)
    )

    now = summary(conn, "24h")["now"]

    assert (now["running"], now["expired_leases"]) == (1, 1)
    assert now["last_worker_activity"] is not None
    assert now["datasets_awaiting_review"] == 1


def test_failures_list_problems_without_user_data(conn: psycopg.Connection, shop: UUID) -> None:
    job(conn, shop, status="succeeded", result=answered())
    failed = job(conn, shop, status="failed", error="AllRoutesFailedError: groq: 503", attempts=3)
    unverified = job(conn, shop, status="succeeded", result=stopped("unverified"), age="2 hours")
    retrying = job(
        conn, shop, status="queued", error="TransientLLMError: timeout", attempts=1, age="30 min"
    )
    conn.execute("UPDATE jobs SET trace_ids = '{abc}' WHERE id = %s", (failed,))

    rows = failures(conn, limit=10)

    assert [row["id"] for row in rows] == [str(retrying), str(failed), str(unverified)]
    first = rows[1]
    assert first["workspace_name"] == "Coffee shop" and first["kind"] == "run"
    assert first["status"] == "failed" and first["attempts"] == 3
    assert first["error"] == "AllRoutesFailedError: groq: 503"
    assert first["trace_ids"] == ["abc"]
    assert rows[2]["stop_reason"] == "unverified" and rows[2]["error"] is None
    assert "secret question" not in str(rows) and "the numbers" not in str(rows)

    older = failures(conn, limit=2, before=rows[1]["created_at"])
    assert [row["id"] for row in older] == [str(unverified)]


def test_long_errors_are_cut(conn: psycopg.Connection, shop: UUID) -> None:
    job(conn, shop, status="failed", error="x" * 1000)

    assert len(failures(conn, limit=1)[0]["error"]) == 300


def test_unknown_windows_are_refused(conn: psycopg.Connection) -> None:
    with pytest.raises(ValueError, match="window"):
        summary(conn, "1y")
