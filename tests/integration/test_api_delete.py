"""Deleting a dataset over HTTP: it is hidden at once, every route of it answers 404, and the
delete itself is never refused for lack of budget or job slots. Needs Postgres
(DA_AGENT_TEST_POSTGRES_URL). See docs/design/2026-11-24-dataset-deletion.md.
"""

from typing import Any
from uuid import UUID

import psycopg
import pytest
from fastapi.testclient import TestClient

from da_agent.storage.datasets import DatasetStore
from da_agent.storage.keys import create_key
from da_agent.storage.limits import RateLimiter
from da_agent.storage.workspaces import create_workspace, set_daily_budget
from tests.integration.test_api import audit_actions, auth, ready_dataset
from tests.integration.test_api_exports import _complete, client, exports, finished_run, key, start
from tests.integration.test_api_queries import ROWS, ask

pytestmark = pytest.mark.integration
__all__ = ["client", "exports", "key"]  # fixtures shared with the export tests


def delete_jobs(conn: psycopg.Connection) -> list[tuple[str, str]]:
    rows = conn.execute("SELECT kind, status FROM jobs WHERE kind = 'delete' ORDER BY created_at")
    return [(row[0], row[1]) for row in rows]


def test_delete_hides_the_dataset_at_once_and_is_logged(
    client: TestClient, conn: psycopg.Connection, key: str
) -> None:
    d = ready_dataset(client, conn, key)

    r = client.delete(f"/v1/datasets/{d}", headers=auth(key))

    assert r.status_code == 202 and r.json() == {"id": d, "status": "deleting"}
    listed = client.get("/v1/datasets", headers=auth(key)).json()["datasets"]
    assert d not in [x["id"] for x in listed]
    assert "dataset.delete" in audit_actions(conn)
    assert delete_jobs(conn) == [("delete", "queued")]


def test_a_malformed_id_is_not_found(client: TestClient, key: str) -> None:
    assert client.delete("/v1/datasets/nope", headers=auth(key)).status_code == 404


ROUTES: list[tuple[str, str, dict[str, Any]]] = [
    ("GET", "/v1/datasets/{d}", {}),
    ("GET", "/v1/datasets/{d}/versions", {}),
    ("GET", "/v1/datasets/{d}/memory", {}),
    ("GET", "/v1/runs?dataset_id={d}", {}),
    ("GET", "/v1/runs/{run}", {}),
    ("GET", "/v1/runs/{run}/events", {}),
    ("GET", "/v1/runs/{run}/report", {}),
    ("POST", "/v1/runs", {"json": {"dataset_id": "{d}", "question": "Totals?"}}),
    ("POST", "/v1/datasets/{d}/files", {"files": [("files", ("a.csv", b"id\n1\n"))]}),
    ("POST", "/v1/datasets/{d}/approve", {"json": {}}),
    ("POST", "/v1/datasets/{d}/versions/1/approve", {"json": {}}),
    ("POST", "/v1/datasets/{d}/versions/1/discard", {}),
    ("POST", "/v1/runs/{run}/queries/0/export", {}),
    ("POST", "/v1/runs/{run}/queries", {"json": {"sql": "SELECT total FROM orders"}}),
    ("POST", "/v1/runs/{run}/cancel", {}),
    ("PUT", "/v1/runs/{run}/feedback", {"json": {"rating": "up"}}),
    ("DELETE", "/v1/runs/{run}/feedback", {}),
    ("GET", "/v1/queries/{query}", {}),
    ("POST", "/v1/queries/{query}/export", {}),
    ("GET", "/v1/exports/{export}", {}),
    ("GET", "/v1/exports/{export}/file", {}),
    ("POST", "/v1/conversations", {"json": {"dataset_id": "{d}"}}),
    ("POST", "/v1/datasets/{d}/memory", {"json": {"kind": "correction", "text": "Use total."}}),
    (
        "POST",
        "/v1/datasets/{d}/metrics",
        {"json": {"run_id": "{run}", "query_index": 0, "candidate_index": 0, "name": "Sum"}},
    ),
    ("POST", "/v1/memory/{memory}/approve", {}),
    ("POST", "/v1/memory/{memory}/reject", {}),
    ("PATCH", "/v1/memory/{memory}", {"json": {"text": "Use total, not amount."}}),
    ("DELETE", "/v1/memory/{memory}", {}),
]


def _fill(value: Any, ids: dict[str, str]) -> Any:
    if isinstance(value, str):
        return value.format(**ids)
    if isinstance(value, dict):
        return {k: _fill(v, ids) for k, v in value.items()}
    return value


@pytest.mark.parametrize(("method", "path", "extra"), ROUTES)
def test_every_route_of_a_deleting_dataset_is_not_found(
    client: TestClient,
    conn: psycopg.Connection,
    key: str,
    method: str,
    path: str,
    extra: dict[str, Any],
) -> None:
    run = finished_run(client, conn, key)
    row = conn.execute("SELECT id FROM datasets").fetchone()
    assert row is not None
    d = str(row[0])
    query = ask(client, key, run).json()["id"]
    _complete(conn, lambda job: ROWS)
    export = start(client, key, run).json()["id"]
    _complete(conn, lambda job: {"rows": 1, "bytes": 1, "filename": "a.csv"})
    memory = client.post(
        f"/v1/datasets/{d}/memory",
        headers=auth(key),
        json={"kind": "correction", "text": "Use total."},
    ).json()["id"]
    ids = {"d": d, "run": run, "query": query, "export": export, "memory": memory}
    assert client.delete(f"/v1/datasets/{d}", headers=auth(key)).status_code == 202

    response = client.request(method, _fill(path, ids), headers=auth(key), **_fill(extra, ids))

    assert response.status_code == 404, response.text


def test_deleting_twice_queues_one_job_and_a_failed_delete_can_be_retried(
    client: TestClient, conn: psycopg.Connection, key: str
) -> None:
    d = ready_dataset(client, conn, key)
    assert client.delete(f"/v1/datasets/{d}", headers=auth(key)).status_code == 202
    assert client.delete(f"/v1/datasets/{d}", headers=auth(key)).status_code == 202
    assert delete_jobs(conn) == [("delete", "queued")]

    conn.execute("UPDATE jobs SET status = 'failed' WHERE kind = 'delete'")
    assert client.delete(f"/v1/datasets/{d}", headers=auth(key)).status_code == 202

    assert delete_jobs(conn) == [("delete", "failed"), ("delete", "queued")]


def test_another_workspace_cannot_delete_a_dataset(
    client: TestClient, conn: psycopg.Connection, key: str
) -> None:
    d = ready_dataset(client, conn, key)
    other = create_key(conn, create_workspace(conn, "other"))

    response = client.delete(f"/v1/datasets/{d}", headers=auth(other))

    assert response.status_code == 404
    status = conn.execute("SELECT status FROM datasets WHERE id = %s", (d,)).fetchone()
    assert status is not None and status[0] == "ready"
    assert delete_jobs(conn) == []


def test_delete_works_with_no_budget_left_and_at_the_job_limit(
    client: TestClient, conn: psycopg.Connection, key: str, workspace: UUID
) -> None:
    d = ready_dataset(client, conn, key)
    set_daily_budget(conn, workspace, 0)
    for _ in range(1000):
        RateLimiter(conn).hit(f"jobs:{workspace}", limit=30, window_seconds=60)
    body = {"dataset_id": d, "question": "Totals?"}
    assert client.post("/v1/runs", headers=auth(key), json=body).status_code == 429

    response = client.delete(f"/v1/datasets/{d}", headers=auth(key))

    assert response.status_code == 202


def test_a_delete_between_the_check_and_the_enqueue_leaves_no_job(
    client: TestClient,
    conn: psycopg.Connection,
    key: str,
    workspace: UUID,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    d = ready_dataset(client, conn, key)
    from da_agent.api import app as api

    real = api._may_start_job

    def delete_first(*args: Any, **kwargs: Any) -> None:
        real(*args, **kwargs)
        DatasetStore(conn).start_delete(workspace, UUID(d))

    monkeypatch.setattr(api, "_may_start_job", delete_first)

    response = client.post(
        "/v1/runs", headers=auth(key), json={"dataset_id": d, "question": "Totals?"}
    )

    assert response.status_code == 404
    count = conn.execute("SELECT count(*) FROM jobs WHERE kind = 'run'").fetchone()
    assert count is not None and count[0] == 0
