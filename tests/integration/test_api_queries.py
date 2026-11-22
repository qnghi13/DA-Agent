"""Edited SQL over HTTP: checked before it is queued, run as a job, exported like a run's query.

Jobs are completed here directly instead of by a worker. Needs Postgres
(DA_AGENT_TEST_POSTGRES_URL). See docs/design/2026-11-14-edit-sql.md.
"""

from typing import Any
from uuid import UUID

import psycopg
import pytest
from fastapi.testclient import TestClient

from da_agent.storage.exports import CHANGED_DATASET
from da_agent.storage.jobs import JobQueue
from da_agent.storage.keys import create_key
from da_agent.storage.workspaces import create_workspace
from tests.integration.test_api import audit_actions, auth
from tests.integration.test_api_exports import _complete, client, exports, finished_run, key

pytestmark = pytest.mark.integration
__all__ = ["client", "exports", "key"]  # fixtures shared with the export tests

SQL = "SELECT total FROM orders WHERE total > 100"
ROWS = {"sql": SQL, "columns": ["total"], "rows": [[250]], "truncated": False}


def ask(client: TestClient, key: str, run_id: str, sql: str = SQL) -> Any:
    return client.post(f"/v1/runs/{run_id}/queries", headers=auth(key), json={"sql": sql})


def jobs_of(conn: psycopg.Connection, kind: str) -> int:
    row = conn.execute("SELECT count(*) FROM jobs WHERE kind = %s", (kind,)).fetchone()
    return row[0] if row else 0


def finished_query(client: TestClient, conn: psycopg.Connection, key: str) -> tuple[str, str]:
    run_id = finished_run(client, conn, key)
    query_id = ask(client, key, run_id).json()["id"]
    _complete(conn, lambda job: ROWS)
    return run_id, query_id


def test_an_edited_query_is_queued_and_logged_without_its_text(
    client: TestClient, conn: psycopg.Connection, key: str, workspace: UUID
) -> None:
    run_id = finished_run(client, conn, key)

    response = ask(client, key, run_id, SQL + ";")

    assert response.status_code == 202
    body = response.json()
    job = JobQueue(conn).get(UUID(body["id"]), workspace)
    assert job is not None and job.kind == "query"
    assert job.payload["run_id"] == run_id and job.payload["sql"] == SQL
    assert "query.create" in audit_actions(conn)
    details = [str(row[0]) for row in conn.execute("SELECT detail FROM audit_log")]
    assert not any("orders" in detail for detail in details)
    shown = client.get(f"/v1/queries/{body['id']}", headers=auth(key)).json()
    assert shown == {"id": body["id"], "status": "queued", "sql": SQL, "columns": [],
                     "rows": [], "truncated": False, "error": None}  # fmt: skip


@pytest.mark.parametrize(
    ("sql", "words"),
    [
        ("SELEC total FROM orders", "could not parse the query"),
        ("doanh thu tháng 6 là bao nhiêu", "could not parse the query"),
        ("DROP TABLE orders", "only SELECT queries are allowed"),
        ("SELECT 1; DELETE FROM orders", "expected one statement, found 2"),
        ("SELECT * FROM read_csv('/etc/passwd')", "query the dataset's tables"),
    ],
)
def test_bad_sql_is_422_with_the_reason_and_queues_nothing(
    client: TestClient, conn: psycopg.Connection, key: str, sql: str, words: str
) -> None:
    run_id = finished_run(client, conn, key)

    response = ask(client, key, run_id, sql)

    assert response.status_code == 422
    assert words in response.json()["detail"]
    assert "\x1b" not in response.json()["detail"]
    assert jobs_of(conn, "query") == 0


@pytest.mark.parametrize("sql", ["", "   ", "SELECT " + "1, " * 10_000 + "1"])
def test_empty_or_huge_sql_is_422(
    client: TestClient, conn: psycopg.Connection, key: str, sql: str
) -> None:
    run_id = finished_run(client, conn, key)

    assert ask(client, key, run_id, sql).status_code == 422
    assert jobs_of(conn, "query") == 0


def test_an_unfinished_run_is_409(client: TestClient, conn: psycopg.Connection, key: str) -> None:
    run_id = finished_run(client, conn, key)
    conn.execute("UPDATE jobs SET status = 'running' WHERE id = %s", (run_id,))

    assert ask(client, key, run_id).status_code == 409


def test_a_dataset_that_is_not_ready_is_409(
    client: TestClient, conn: psycopg.Connection, key: str
) -> None:
    run_id = finished_run(client, conn, key)
    conn.execute("UPDATE datasets SET status = 'building'")

    response = ask(client, key, run_id)

    assert response.status_code == 409
    assert response.json()["detail"] == "the dataset is not ready"
    assert response.json()["code"] == "dataset.not_ready"


def test_a_query_starts_when_the_model_budget_is_spent(
    client: TestClient, conn: psycopg.Connection, key: str, workspace: UUID
) -> None:
    run_id = finished_run(client, conn, key)
    conn.execute("UPDATE workspaces SET daily_budget_usd = 0 WHERE id = %s", (workspace,))

    assert ask(client, key, run_id).status_code == 202


def test_a_finished_query_shows_its_rows(
    client: TestClient, conn: psycopg.Connection, key: str
) -> None:
    _, query_id = finished_query(client, conn, key)

    shown = client.get(f"/v1/queries/{query_id}", headers=auth(key)).json()

    assert shown == {"id": query_id, "status": "succeeded", "sql": SQL, "columns": ["total"],
                     "rows": [[250]], "truncated": False, "error": None}  # fmt: skip


def test_a_failed_query_shows_the_engine_message(
    client: TestClient, conn: psycopg.Connection, key: str
) -> None:
    run_id = finished_run(client, conn, key)
    query_id = ask(client, key, run_id).json()["id"]
    queue = JobQueue(conn)
    job = queue.claim("w", lease_seconds=30)
    assert job is not None
    message = 'Binder Error: Referenced column "totl" not found'
    queue.fail(job.id, "w", message, retry_in_seconds=None)

    shown = client.get(f"/v1/queries/{query_id}", headers=auth(key)).json()

    assert shown["status"] == "failed" and shown["error"] == message


def test_a_finished_query_exports_by_id(
    client: TestClient, conn: psycopg.Connection, key: str, workspace: UUID
) -> None:
    _, query_id = finished_query(client, conn, key)

    response = client.post(f"/v1/queries/{query_id}/export", headers=auth(key))

    assert response.status_code == 202
    job = JobQueue(conn).get(UUID(response.json()["id"]), workspace)
    assert job is not None and job.kind == "export"
    assert job.payload["query_id"] == query_id and "sql" not in job.payload
    assert client.get(f"/v1/exports/{job.id}", headers=auth(key)).json()["status"] == "queued"


def test_an_unfinished_query_cannot_be_exported(
    client: TestClient, conn: psycopg.Connection, key: str
) -> None:
    run_id = finished_run(client, conn, key)
    query_id = ask(client, key, run_id).json()["id"]

    assert client.post(f"/v1/queries/{query_id}/export", headers=auth(key)).status_code == 409


def test_an_edited_query_on_a_changed_dataset_cannot_be_exported(
    client: TestClient, conn: psycopg.Connection, key: str
) -> None:
    _, query_id = finished_query(client, conn, key)
    conn.execute("UPDATE datasets SET updated_at = now() + interval '1 minute'")

    response = client.post(f"/v1/queries/{query_id}/export", headers=auth(key))

    assert response.status_code == 409
    assert response.json()["detail"] == CHANGED_DATASET


@pytest.mark.security
def test_another_workspace_gets_404(client: TestClient, conn: psycopg.Connection, key: str) -> None:
    run_id, query_id = finished_query(client, conn, key)
    other = create_key(conn, create_workspace(conn, "other"))

    assert ask(client, other, run_id).status_code == 404
    assert client.get(f"/v1/queries/{query_id}", headers=auth(other)).status_code == 404
    assert client.post(f"/v1/queries/{query_id}/export", headers=auth(other)).status_code == 404


def test_a_run_id_is_not_a_query_id(client: TestClient, conn: psycopg.Connection, key: str) -> None:
    run_id = finished_run(client, conn, key)

    assert client.get(f"/v1/queries/{run_id}", headers=auth(key)).status_code == 404
    assert client.post(f"/v1/queries/{run_id}/export", headers=auth(key)).status_code == 404
