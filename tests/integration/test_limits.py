"""Rate limits and money limits: per key, per workspace, per client address, and per day.

Needs Postgres (DA_AGENT_TEST_POSTGRES_URL).
"""

import threading
from pathlib import Path
from uuid import UUID

import psycopg
import pytest
from fastapi.testclient import TestClient

from da_agent.api.app import ApiSettings, create_app
from da_agent.llm.client import CostRecord
from da_agent.storage.jobs import Job, JobQueue
from da_agent.storage.keys import create_key
from da_agent.storage.ledger import PostgresCostLedger, spent_today
from da_agent.storage.limits import RateLimiter
from da_agent.storage.workspaces import create_workspace, set_daily_budget
from da_agent.worker.limits import WorkspaceBudgets
from da_agent.worker.worker import PermanentJobError
from tests.integration.conftest import POSTGRES_URL

pytestmark = pytest.mark.integration


def call(run_id: str, cost: float, tokens: int = 100) -> CostRecord:
    return CostRecord(
        run_id=run_id, provider="fake", model="m", input_tokens=tokens, output_tokens=0,
        cached_input_tokens=0, cost_usd=cost, latency_seconds=0.1,
    )  # fmt: skip


def test_a_window_allows_its_limit_then_says_when_to_retry(conn: psycopg.Connection) -> None:
    limiter = RateLimiter(conn)

    allowed = [limiter.hit("key:a", limit=3, window_seconds=60) for _ in range(3)]
    refused = limiter.hit("key:a", limit=3, window_seconds=60)

    assert allowed == [None, None, None]
    assert refused is not None and 0 < refused <= 60
    assert limiter.hit("key:b", limit=3, window_seconds=60) is None


def test_concurrent_hits_are_all_counted(schema: str, conn: psycopg.Connection) -> None:
    from da_agent.storage.db import connect

    refused: list[float] = []
    lock = threading.Lock()

    def hammer() -> None:
        assert POSTGRES_URL is not None
        with connect(POSTGRES_URL, schema=schema) as own:
            for _ in range(10):
                wait = RateLimiter(own).hit("key:shared", limit=25, window_seconds=60)
                if wait is not None:
                    with lock:
                        refused.append(wait)

    threads = [threading.Thread(target=hammer) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(refused) == 15


def test_the_ledger_keeps_a_runs_cost_across_processes(
    conn: psycopg.Connection, workspace: UUID
) -> None:
    job_id = JobQueue(conn).enqueue(workspace, "run", {})
    PostgresCostLedger(conn).record(call(str(job_id), 0.02, tokens=300))

    # Another worker, with its own ledger, sees what the first attempt spent.
    totals = PostgresCostLedger(conn).totals(str(job_id))

    assert (totals.calls, totals.total_tokens, totals.cost_usd) == (1, 300, pytest.approx(0.02))
    assert spent_today(conn, workspace) == pytest.approx(0.02)


def test_spending_is_counted_per_workspace(conn: psycopg.Connection, workspace: UUID) -> None:
    other = create_workspace(conn, "other")
    ledger = PostgresCostLedger(conn)
    ledger.record(call(str(JobQueue(conn).enqueue(workspace, "run", {})), 0.05))
    ledger.record(call(str(JobQueue(conn).enqueue(other, "run", {})), 0.01))

    assert spent_today(conn, workspace) == pytest.approx(0.05)
    assert spent_today(conn, other) == pytest.approx(0.01)


def job_for(conn: psycopg.Connection, workspace: UUID) -> Job:
    queue = JobQueue(conn)
    queue.enqueue(workspace, "run", {})
    job = queue.claim("w", lease_seconds=30)
    assert job is not None
    return job


def test_a_run_gets_what_is_left_of_the_day_up_to_the_run_limit(
    schema: str, conn: psycopg.Connection, workspace: UUID
) -> None:
    assert POSTGRES_URL is not None
    budgets = WorkspaceBudgets(
        url=POSTGRES_URL, schema=schema, daily_usd=1.0, run_usd=0.30, run_tokens=50_000
    )
    job = job_for(conn, workspace)
    assert budgets.for_job(job).max_cost_usd == pytest.approx(0.30)

    PostgresCostLedger(conn).record(call(str(job.id), 0.90))

    budget = budgets.for_job(job_for(conn, workspace))
    assert budget.max_cost_usd == pytest.approx(0.10)
    assert budget.max_total_tokens == 50_000


def test_a_workspace_over_its_daily_budget_gets_no_new_model_calls(
    schema: str, conn: psycopg.Connection, workspace: UUID
) -> None:
    assert POSTGRES_URL is not None
    set_daily_budget(conn, workspace, 0.05)
    budgets = WorkspaceBudgets(url=POSTGRES_URL, schema=schema, daily_usd=1.0, run_usd=0.30)
    job = job_for(conn, workspace)
    PostgresCostLedger(conn).record(call(str(job.id), 0.05))

    with pytest.raises(PermanentJobError, match="daily"):
        budgets.for_job(job_for(conn, workspace))


# Through the API


@pytest.fixture
def settings(schema: str, tmp_path: Path) -> ApiSettings:
    assert POSTGRES_URL is not None
    return ApiSettings(
        database_url=POSTGRES_URL,
        schema=schema,
        uploads_root=tmp_path / "uploads",
        requests_per_minute=5,
        jobs_per_minute=2,
        failed_auth_per_minute=3,
        daily_budget_usd=1.0,
    )


@pytest.fixture
def client(settings: ApiSettings) -> TestClient:
    return TestClient(create_app(settings))


def auth(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def test_a_key_over_its_request_rate_is_told_to_wait(
    client: TestClient, conn: psycopg.Connection, workspace: UUID
) -> None:
    key, other = create_key(conn, workspace), create_key(conn, workspace)

    codes = [client.get("/v1/datasets", headers=auth(key)).status_code for _ in range(6)]

    assert codes == [200] * 5 + [429]
    refused = client.get("/v1/datasets", headers=auth(key))
    assert int(refused.headers["retry-after"]) > 0
    assert client.get("/v1/datasets", headers=auth(other)).status_code == 200


def test_new_jobs_have_their_own_lower_rate_per_workspace(
    client: TestClient, conn: psycopg.Connection, workspace: UUID
) -> None:
    key = create_key(conn, workspace)

    def send() -> int:
        files = [("files", ("a.csv", b"a\n1\n"))]
        return client.post(
            "/v1/datasets", headers=auth(key), data={"name": "x"}, files=files
        ).status_code

    assert [send() for _ in range(3)] == [202, 202, 429]


@pytest.mark.security
def test_repeated_wrong_keys_from_one_address_are_slowed(
    client: TestClient, conn: psycopg.Connection, workspace: UUID
) -> None:
    key = create_key(conn, workspace)
    wrong = auth(key[:-1] + ("A" if key[-1] != "A" else "B"))

    codes = [client.get("/v1/datasets", headers=wrong).status_code for _ in range(4)]

    assert codes == [401, 401, 401, 429]
    # Guessing stays blocked for the window, even with a valid key from the same address.
    assert client.get("/v1/datasets", headers=auth(key)).status_code == 429


def test_a_workspace_over_its_daily_budget_cannot_start_jobs(
    client: TestClient, conn: psycopg.Connection, workspace: UUID
) -> None:
    key = create_key(conn, workspace)
    job_id = JobQueue(conn).enqueue(workspace, "run", {})
    PostgresCostLedger(conn).record(call(str(job_id), 1.5))
    files = [("files", ("a.csv", b"a\n1\n"))]

    response = client.post("/v1/datasets", headers=auth(key), data={"name": "x"}, files=files)

    assert response.status_code == 429
    assert "budget" in response.json()["detail"]
    assert client.get("/v1/datasets", headers=auth(key)).status_code == 200
