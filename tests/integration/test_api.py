"""The HTTP API: workspace keys, uploads, spec approval, runs, reports, audit, and isolation.

Handlers only read and write Postgres and the uploads directory; jobs are completed here directly
instead of by a worker. Needs Postgres (DA_AGENT_TEST_POSTGRES_URL).
"""

import json
import threading
import time
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
import pytest
from fastapi.testclient import TestClient

from da_agent.agent.answer import AgentAnswer, AgentResult, QueryRecord, ToolStep, result_to_json
from da_agent.api.app import ApiSettings, create_app
from da_agent.storage.datasets import DatasetStore
from da_agent.storage.db import connect
from da_agent.storage.events import RunEvents
from da_agent.storage.jobs import JobQueue
from da_agent.storage.keys import create_key, revoke_key
from da_agent.storage.workspaces import create_workspace
from tests.integration.conftest import POSTGRES_URL

pytestmark = pytest.mark.integration

SPEC = {
    "tables": [
        {
            "source": "don",
            "name": "orders",
            "columns": [{"source": "Tổng", "name": "total", "type": "money"}],
        }
    ]
}


@pytest.fixture
def settings(schema: str, tmp_path: Path) -> ApiSettings:
    assert POSTGRES_URL is not None
    return ApiSettings(
        database_url=POSTGRES_URL,
        schema=schema,
        uploads_root=tmp_path / "uploads",
        max_files=3,
        max_file_bytes=1000,
    )


@pytest.fixture
def client(settings: ApiSettings) -> TestClient:
    return TestClient(create_app(settings))


@pytest.fixture
def key(conn: psycopg.Connection, workspace: UUID) -> str:
    return create_key(conn, workspace)


def auth(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def upload(client: TestClient, key: str, files: list[tuple[str, bytes]] | None = None) -> Any:
    sent = files or [("Đơn hàng.csv", b"id,total\n1,100\n"), ("README.md", b"notes")]
    return client.post(
        "/v1/datasets",
        headers=auth(key),
        data={"name": "Shop", "description": "Revenue excludes cancelled orders."},
        files=[("files", (name, body)) for name, body in sent],
    )


def proposed_dataset(client: TestClient, conn: psycopg.Connection, key: str) -> str:
    dataset_id = upload(client, key).json()["id"]
    DatasetStore(conn).mark_proposed(UUID(dataset_id), SPEC, {"tables": []}, [])
    return dataset_id


def ready_dataset(client: TestClient, conn: psycopg.Connection, key: str) -> str:
    dataset_id = proposed_dataset(client, conn, key)
    assert (
        client.post(f"/v1/datasets/{dataset_id}/approve", headers=auth(key), json={}).status_code
        == 200
    )
    return dataset_id


def audit_actions(conn: psycopg.Connection) -> list[str]:
    return [row[0] for row in conn.execute("SELECT action FROM audit_log ORDER BY id")]


# Keys


def test_requests_without_a_valid_key_are_refused_and_logged(
    client: TestClient, conn: psycopg.Connection, key: str
) -> None:
    assert client.get("/v1/datasets").status_code == 401
    assert client.get("/v1/datasets", headers=auth("dak_00000000_wrong")).status_code == 401
    assert client.get("/v1/datasets", headers=auth(key + "x")).status_code == 401

    assert audit_actions(conn).count("auth.failed") == 3


def test_a_key_is_stored_only_as_a_hash(conn: psycopg.Connection, key: str) -> None:
    stored = conn.execute("SELECT prefix, key_hash FROM api_keys").fetchall()

    assert len(stored) == 1
    assert key not in str(stored)
    assert key.startswith(f"dak_{stored[0][0]}_")


def test_a_revoked_key_stops_working(
    client: TestClient, conn: psycopg.Connection, key: str
) -> None:
    assert client.get("/v1/datasets", headers=auth(key)).status_code == 200

    revoke_key(conn, key)

    assert client.get("/v1/datasets", headers=auth(key)).status_code == 401


# Uploads


def test_an_upload_stores_files_and_queues_ingestion(
    client: TestClient, conn: psycopg.Connection, key: str, settings: ApiSettings, workspace: UUID
) -> None:
    response = upload(client, key)

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "uploaded"
    stored = sorted(p.name for p in (settings.uploads_root / body["id"]).iterdir())
    assert stored == ["Don_hang.csv", "README.md"]
    assert body["files"][0] == {"name": "Đơn hàng.csv", "stored_as": "Don_hang.csv", "bytes": 15}
    job = JobQueue(conn).claim("w", lease_seconds=30)
    assert job is not None and (job.kind, job.payload) == ("ingest", {"dataset_id": body["id"]})
    assert job.workspace_id == workspace
    assert "dataset.upload" in audit_actions(conn)


@pytest.mark.security
def test_upload_names_cannot_leave_the_upload_directory(
    client: TestClient, key: str, settings: ApiSettings
) -> None:
    response = upload(client, key, [("../../../escape.csv", b"a\n1\n")])

    assert response.status_code == 202
    assert not (settings.uploads_root.parent / "escape.csv").exists()
    assert (settings.uploads_root / response.json()["id"] / "escape.csv").is_file()


@pytest.mark.parametrize(
    ("files", "status"),
    [
        ([("run.sh", b"rm -rf /")], 400),
        ([("big.csv", b"x" * 1001)], 413),
        ([(f"f{i}.csv", b"a\n") for i in range(4)], 413),
        ([], 422),
    ],
)
def test_bad_uploads_are_refused_and_leave_nothing_behind(
    client: TestClient,
    conn: psycopg.Connection,
    key: str,
    settings: ApiSettings,
    files: list[tuple[str, bytes]],
    status: int,
) -> None:
    sent = files or None
    response = (
        upload(client, key, sent)
        if sent
        else client.post("/v1/datasets", headers=auth(key), data={"name": "Shop"})
    )

    assert response.status_code == status
    assert not settings.uploads_root.exists() or not any(settings.uploads_root.iterdir())
    assert conn.execute("SELECT count(*) FROM datasets").fetchone() == (0,)


# Review


def test_a_dataset_shows_its_review_and_can_be_approved(
    client: TestClient, conn: psycopg.Connection, key: str
) -> None:
    dataset_id = proposed_dataset(client, conn, key)

    shown = client.get(f"/v1/datasets/{dataset_id}", headers=auth(key)).json()
    assert (shown["status"], shown["spec"]) == ("proposed", SPEC)
    approved = client.post(f"/v1/datasets/{dataset_id}/approve", headers=auth(key), json={})

    assert approved.status_code == 200 and approved.json()["status"] == "ready"
    listed = client.get("/v1/datasets", headers=auth(key)).json()
    assert [d["id"] for d in listed["datasets"]] == [dataset_id]
    assert "dataset.approve" in audit_actions(conn)


def test_approval_errors_are_explained(
    client: TestClient, conn: psycopg.Connection, key: str
) -> None:
    uploaded = upload(client, key).json()["id"]
    assert (
        client.post(f"/v1/datasets/{uploaded}/approve", headers=auth(key), json={}).status_code
        == 409
    )

    proposed = proposed_dataset(client, conn, key)
    invalid = client.post(
        f"/v1/datasets/{proposed}/approve", headers=auth(key), json={"spec": {"tables": "x"}}
    )
    assert invalid.status_code == 422 and "spec" in invalid.json()["detail"]


# Runs


def test_a_question_about_a_ready_dataset_queues_a_run(
    client: TestClient, conn: psycopg.Connection, key: str
) -> None:
    dataset_id = ready_dataset(client, conn, key)
    JobQueue(conn).claim("w", lease_seconds=30)  # the ingest job

    response = client.post(
        "/v1/runs",
        headers=auth(key),
        json={"dataset_id": dataset_id, "question": "Total revenue?", "as_of": "2026-06-28"},
    )

    assert response.status_code == 202
    run = JobQueue(conn).claim("w", lease_seconds=30)
    assert run is not None and run.id == UUID(response.json()["id"])
    assert run.payload == {
        "dataset_id": dataset_id,
        "question": "Total revenue?",
        "as_of": "2026-06-28",
        "charts": True,
    }
    assert "run.create" in audit_actions(conn)


def test_a_question_needs_a_ready_dataset(
    client: TestClient, conn: psycopg.Connection, key: str
) -> None:
    dataset_id = proposed_dataset(client, conn, key)

    response = client.post(
        "/v1/runs", headers=auth(key), json={"dataset_id": dataset_id, "question": "Total?"}
    )

    assert response.status_code == 409


def test_a_finished_run_shows_its_answer_and_report(
    client: TestClient, conn: psycopg.Connection, key: str
) -> None:
    dataset_id = ready_dataset(client, conn, key)
    run_id = client.post(
        "/v1/runs", headers=auth(key), json={"dataset_id": dataset_id, "question": "Total?"}
    ).json()["id"]
    assert client.get(f"/v1/runs/{run_id}", headers=auth(key)).json()["status"] == "queued"
    query = QueryRecord(sql="SELECT SUM(total) AS t FROM orders", columns=["t"], rows=[[350]])
    result = AgentResult(
        answer=AgentAnswer("number", 350),
        steps=[ToolStep(tool="run_sql", ok=True, sql=query.sql, row_count=1)],
        queries=[query],
    )
    queue = JobQueue(conn)
    while (job := queue.claim("w", lease_seconds=30)) is not None:
        queue.complete(job.id, "w", result_to_json(result) if job.kind == "run" else {})

    shown = client.get(f"/v1/runs/{run_id}", headers=auth(key)).json()
    report = client.get(f"/v1/runs/{run_id}/report", headers=auth(key))

    assert shown["status"] == "succeeded"
    assert shown["result"]["answer"] == {"kind": "number", "value": 350, "reason": None}
    assert report.status_code == 200 and report.headers["content-type"].startswith("text/html")
    assert "default-src 'none'" in report.headers["content-security-policy"]
    assert 'href="#q1-r1-c1"' in report.text and "Total?" in report.text


def test_a_report_waits_for_the_run_to_finish(
    client: TestClient, conn: psycopg.Connection, key: str
) -> None:
    dataset_id = ready_dataset(client, conn, key)
    run_id = client.post(
        "/v1/runs", headers=auth(key), json={"dataset_id": dataset_id, "question": "Total?"}
    ).json()["id"]

    assert client.get(f"/v1/runs/{run_id}/report", headers=auth(key)).status_code == 409


# Isolation


@pytest.mark.security
def test_a_workspace_cannot_see_or_use_another_workspaces_data(
    client: TestClient, conn: psycopg.Connection, key: str
) -> None:
    dataset_id = ready_dataset(client, conn, key)
    run_id = client.post(
        "/v1/runs", headers=auth(key), json={"dataset_id": dataset_id, "question": "Total?"}
    ).json()["id"]
    intruder = auth(create_key(conn, create_workspace(conn, "intruder")))

    assert client.get(f"/v1/datasets/{dataset_id}", headers=intruder).status_code == 404
    assert client.get("/v1/datasets", headers=intruder).json() == {"datasets": []}
    assert (
        client.post(f"/v1/datasets/{dataset_id}/approve", headers=intruder, json={}).status_code
        == 404
    )
    assert (
        client.post(
            "/v1/runs", headers=intruder, json={"dataset_id": dataset_id, "question": "x"}
        ).status_code
        == 404
    )
    assert client.get(f"/v1/runs/{run_id}", headers=intruder).status_code == 404
    assert client.get(f"/v1/runs/{run_id}/report", headers=intruder).status_code == 404


@pytest.mark.security
def test_a_dataset_job_id_is_not_a_run(
    client: TestClient, conn: psycopg.Connection, key: str
) -> None:
    upload(client, key)
    ingest = JobQueue(conn).claim("w", lease_seconds=30)
    assert ingest is not None

    assert client.get(f"/v1/runs/{ingest.id}", headers=auth(key)).status_code == 404


def test_malformed_ids_are_not_found(client: TestClient, key: str) -> None:
    assert client.get("/v1/datasets/not-a-uuid", headers=auth(key)).status_code == 404
    assert client.get("/v1/runs/../../etc", headers=auth(key)).status_code == 404


# Progress events


def stream_events(
    client: TestClient, run_id: str, key: str, **headers: str
) -> list[tuple[str, str, dict]]:
    """(id, event, data) for each event of the stream, read until the server ends it."""
    found: list[tuple[str, str, dict]] = []
    with client.stream(
        "GET", f"/v1/runs/{run_id}/events", headers={**auth(key), **headers}
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        current: dict[str, str] = {}
        for line in response.iter_lines():
            if line.startswith(":"):
                continue
            if not line:
                if current:
                    found.append(
                        (
                            current.get("id", ""),
                            current.get("event", ""),
                            json.loads(current.get("data", "{}")),
                        )
                    )
                current = {}
                continue
            field, _, value = line.partition(": ")
            current[field] = value
    return found


def queued_run(client: TestClient, conn: psycopg.Connection, key: str) -> tuple[str, UUID]:
    dataset_id = ready_dataset(client, conn, key)
    run_id = client.post(
        "/v1/runs", headers=auth(key), json={"dataset_id": dataset_id, "question": "Total?"}
    ).json()["id"]
    return run_id, UUID(run_id)


def finish(conn: psycopg.Connection) -> None:
    queue = JobQueue(conn)
    while (job := queue.claim("w", lease_seconds=30)) is not None:
        queue.complete(job.id, "w", {})


def test_events_stream_in_order_and_end_with_the_run_status(
    client: TestClient, conn: psycopg.Connection, key: str, workspace: UUID
) -> None:
    run_id, job_id = queued_run(client, conn, key)
    events = RunEvents(conn)
    events.append(job_id, workspace, {"type": "plan", "steps": ["sum"]})
    events.append(job_id, workspace, {"type": "done", "stop_reason": "answered"})
    finish(conn)

    received = stream_events(client, run_id, key)

    assert [e[1] for e in received] == ["plan", "done", "end"]
    assert received[0][2] == {"type": "plan", "steps": ["sum"]}
    assert received[-1][2] == {"status": "succeeded"}


def test_a_reconnecting_client_gets_only_the_events_it_missed(
    client: TestClient, conn: psycopg.Connection, key: str, workspace: UUID
) -> None:
    run_id, job_id = queued_run(client, conn, key)
    events = RunEvents(conn)
    for step in ("plan", "turn", "tool"):
        events.append(job_id, workspace, {"type": step})
    finish(conn)
    first = stream_events(client, run_id, key)

    again = stream_events(client, run_id, key, **{"Last-Event-ID": first[0][0]})

    assert [e[1] for e in again] == ["turn", "tool", "end"]


def test_events_arrive_while_the_run_is_still_going(
    client: TestClient, conn: psycopg.Connection, key: str, workspace: UUID, schema: str
) -> None:
    run_id, job_id = queued_run(client, conn, key)

    def later() -> None:
        assert POSTGRES_URL is not None
        time.sleep(0.6)
        with connect(POSTGRES_URL, schema=schema) as other:
            RunEvents(other).append(job_id, workspace, {"type": "done", "stop_reason": "answered"})
            finish(other)

    worker = threading.Thread(target=later)
    worker.start()
    received = stream_events(client, run_id, key)
    worker.join()

    assert [e[1] for e in received] == ["done", "end"]


@pytest.mark.security
def test_another_workspace_cannot_stream_a_runs_events(
    client: TestClient, conn: psycopg.Connection, key: str
) -> None:
    run_id, _ = queued_run(client, conn, key)
    intruder = auth(create_key(conn, create_workspace(conn, "intruder")))

    assert client.get(f"/v1/runs/{run_id}/events", headers=intruder).status_code == 404
