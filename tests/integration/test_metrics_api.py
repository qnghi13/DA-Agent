"""API request metrics and metrics read from Postgres (ADR 0044).

Needs a Postgres server. Set DA_AGENT_TEST_POSTGRES_URL, or the tests are skipped.
"""

from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
import pytest
from fastapi.testclient import TestClient
from prometheus_client import CollectorRegistry

from da_agent.api.app import ApiSettings, create_app
from da_agent.llm.client import CostRecord
from da_agent.storage.db import connect
from da_agent.storage.jobs import JobQueue
from da_agent.storage.keys import create_key
from da_agent.storage.ledger import PostgresCostLedger
from da_agent.storage.workspaces import set_daily_budget
from da_agent.telemetry.db_metrics import DatabaseCollector
from da_agent.telemetry.metrics import ApiMetrics
from tests.integration.conftest import POSTGRES_URL

pytestmark = pytest.mark.integration


def metric(registry: CollectorRegistry, name: str, **labels: str) -> float | None:
    return registry.get_sample_value(name, labels)


def test_requests_are_counted_by_route_template_method_and_status(
    schema: str, conn: psycopg.Connection, workspace: UUID, tmp_path: Path
) -> None:
    assert POSTGRES_URL is not None
    registry = CollectorRegistry()
    settings = ApiSettings(
        database_url=POSTGRES_URL, schema=schema, uploads_root=tmp_path / "uploads"
    )
    client = TestClient(create_app(settings, metrics=ApiMetrics(registry)))
    headers = {"Authorization": f"Bearer {create_key(conn, workspace)}"}

    client.get("/v1/runs/00000000-0000-0000-0000-000000000001", headers=headers)
    client.get("/v1/runs/00000000-0000-0000-0000-000000000002", headers=headers)
    client.get("/no/such/path")

    template = {"route": "/v1/runs/{run_id}", "method": "GET", "status": "404"}
    assert metric(registry, "da_agent_http_requests_total", **template) == 2
    assert (
        metric(
            registry, "da_agent_http_requests_total", route="unmatched", method="GET", status="404"
        )
        == 1
    )
    assert (
        metric(registry, "da_agent_http_request_duration_seconds_count", route="/v1/runs/{run_id}")
        == 2
    )


def answered() -> dict[str, Any]:
    return {"stop_reason": "answered", "answer": {"kind": "number", "value": 1}}


def test_the_collector_reports_queue_budget_and_feedback_state(
    schema: str, conn: psycopg.Connection, workspace: UUID
) -> None:
    assert POSTGRES_URL is not None
    queue = JobQueue(conn)
    for _ in range(3):
        queue.enqueue(workspace, "run", {})
    conn.execute(
        "UPDATE jobs SET created_at = now() - interval '10 minutes', "
        "run_after = now() - interval '10 minutes'"
    )
    claimed = queue.claim("w", lease_seconds=30)
    assert claimed is not None
    set_daily_budget(conn, workspace, 2.0)
    PostgresCostLedger(conn).record(
        CostRecord(run_id=str(claimed.id), provider="fake", model="m", input_tokens=1,
                   output_tokens=1, cached_input_tokens=0, cost_usd=1.5, latency_seconds=0.1)
    )  # fmt: skip
    queue.complete(claimed.id, "w", answered())
    conn.execute(
        "INSERT INTO run_feedback (job_id, workspace_id, rating, author) "
        "VALUES (%s, %s, 'up', 'm')",
        (claimed.id, workspace),
    )
    registry = CollectorRegistry()
    registry.register(
        DatabaseCollector(lambda: connect(POSTGRES_URL, schema=schema), default_daily_usd=5.0)
    )

    assert metric(registry, "da_agent_queue_jobs", status="queued") == 2
    assert metric(registry, "da_agent_queue_jobs", status="running") == 0
    oldest = metric(registry, "da_agent_queue_oldest_seconds")
    assert oldest is not None and 590 <= oldest <= 700
    assert metric(
        registry, "da_agent_budget_used_ratio", workspace_id=str(workspace)
    ) == pytest.approx(0.75)
    assert metric(registry, "da_agent_feedback_7d", rating="up") == 1
    assert metric(registry, "da_agent_feedback_7d", rating="down") == 0


def test_the_collector_keeps_its_last_values_when_postgres_is_down(
    schema: str, conn: psycopg.Connection, workspace: UUID
) -> None:
    assert POSTGRES_URL is not None
    JobQueue(conn).enqueue(workspace, "run", {})
    up = [True]
    now = [0.0]

    def connect_or_fail() -> psycopg.Connection:
        if not up[0]:
            raise psycopg.OperationalError("connection refused")
        return connect(POSTGRES_URL, schema=schema)

    collector = DatabaseCollector(connect_or_fail, default_daily_usd=5.0, clock=lambda: now[0])
    registry = CollectorRegistry()
    registry.register(collector)
    assert metric(registry, "da_agent_queue_jobs", status="queued") == 1

    JobQueue(conn).enqueue(workspace, "run", {})
    now[0] = 10.0
    assert metric(registry, "da_agent_queue_jobs", status="queued") == 1  # cached for 30 s
    up[0] = False
    now[0] = 60.0
    assert metric(registry, "da_agent_queue_jobs", status="queued") == 1  # last values kept
    assert metric(registry, "da_agent_db_metrics_up") == 0
    up[0] = True
    now[0] = 120.0
    assert metric(registry, "da_agent_queue_jobs", status="queued") == 2
    assert metric(registry, "da_agent_db_metrics_up") == 1
