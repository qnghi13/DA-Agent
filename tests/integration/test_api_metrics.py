"""Metric definitions over HTTP: saved from a run's own SQL, renamed, listed with a stale flag, and
shown on answers whose SQL used them. Needs Postgres (DA_AGENT_TEST_POSTGRES_URL).

See docs/design/2026-11-15-metric-definitions.md.
"""

from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from da_agent.agent.answer import AgentAnswer, AgentResult, QueryRecord, result_to_json
from da_agent.storage.keys import create_key
from da_agent.storage.workspaces import create_workspace
from tests.integration.test_api import audit_actions, auth, ready_dataset
from tests.integration.test_api_exports import _complete, client, exports, key

pytestmark = pytest.mark.integration
__all__ = ["client", "exports", "key"]  # fixtures shared with the export tests

SPEC = {
    "tables": [
        {"source": "Don hang", "name": "orders", "columns": [
            {"source": "Thành tiền", "name": "total"},
            {"source": "Trạng thái", "name": "status"},
            {"source": "Ngày", "name": "day"},
            {"source": "SĐT", "name": "phone", "sensitive": True},
        ]}
    ]
}  # fmt: skip
REVENUE_SQL = (
    "SELECT SUM(total) AS revenue FROM orders WHERE status <> 'cancelled' "
    "AND day >= DATE '2025-10-01' AND day < DATE '2025-11-01'"
)
REVENUE = AgentResult(
    answer=AgentAnswer("number", 350),
    queries=[
        QueryRecord(sql="SELECT DISTINCT status FROM orders", columns=["status"], rows=[["x"]]),
        QueryRecord(sql=REVENUE_SQL, columns=["revenue"], rows=[[350]]),
    ],
)
JUNE = AgentResult(
    answer=AgentAnswer("number", 120),
    queries=[
        QueryRecord(
            sql="SELECT SUM(total) FROM orders WHERE day >= DATE '2025-06-01' "
            "AND status <> 'cancelled'",
            columns=["s"],
            rows=[[120]],
        )
    ],
)


def dataset(client: TestClient, conn: psycopg.Connection, key: str) -> str:
    dataset_id = ready_dataset(client, conn, key)
    conn.execute("UPDATE datasets SET spec = %s WHERE id = %s", (Jsonb(SPEC), dataset_id))
    return dataset_id


def run(
    client: TestClient, conn: psycopg.Connection, key: str, dataset_id: str, result: AgentResult
) -> str:
    run_id = client.post(
        "/v1/runs", headers=auth(key), json={"dataset_id": dataset_id, "question": "Revenue?"}
    ).json()["id"]
    _complete(conn, lambda job: result_to_json(result) if job.kind == "run" else {})
    return run_id


def view(client: TestClient, key: str, run_id: str) -> dict[str, Any]:
    return client.get(f"/v1/runs/{run_id}", headers=auth(key)).json()


def save(client: TestClient, key: str, dataset_id: str, run_id: str, **changes: Any) -> Any:
    body = {"run_id": run_id, "query_index": 1, "candidate_index": 0, "name": "Doanh thu",
            "aliases": ["DT"]} | changes  # fmt: skip
    return client.post(f"/v1/datasets/{dataset_id}/metrics", headers=auth(key), json=body)


def metrics(client: TestClient, key: str, dataset_id: str) -> list[dict[str, Any]]:
    entries = client.get(f"/v1/datasets/{dataset_id}/memory", headers=auth(key)).json()["memory"]
    return [e for e in entries if e["kind"] == "metric" and e["superseded_by"] is None]


def test_explanation_lists_candidates(
    client: TestClient, conn: psycopg.Connection, key: str
) -> None:
    dataset_id = dataset(client, conn, key)
    [explanation] = view(client, key, run(client, conn, key, dataset_id, REVENUE))["explanations"]
    [candidate] = explanation["metric_candidates"]
    assert candidate["index"] == 0
    assert candidate["facts"]["measure"]["of"]["column"] == "Thành tiền"
    assert [f["op"] for f in candidate["facts"]["filters"]] == ["not_equals"]
    assert explanation["metrics_used"] == []


def test_no_candidates_for_a_join_or_a_hidden_column(
    client: TestClient, conn: psycopg.Connection, key: str
) -> None:
    dataset_id = dataset(client, conn, key)
    result = AgentResult(
        answer=AgentAnswer("number", 7),
        queries=[
            QueryRecord(sql="SELECT COUNT(phone) FROM orders", columns=["n"], rows=[[7]]),
            QueryRecord(
                sql="SELECT SUM(o.total) FROM orders o JOIN orders p ON o.day = p.day",
                columns=["s"],
                rows=[[7]],
            ),
        ],
    )
    explanations = view(client, key, run(client, conn, key, dataset_id, result))["explanations"]
    assert [e["metric_candidates"] for e in explanations] == [[], []]


def test_save_metric_from_a_run(client: TestClient, conn: psycopg.Connection, key: str) -> None:
    dataset_id = dataset(client, conn, key)
    run_id = run(client, conn, key, dataset_id, REVENUE)

    response = save(client, key, dataset_id, run_id)

    assert response.status_code == 201
    [entry] = metrics(client, key, dataset_id)
    assert entry["id"] == response.json()["id"]
    assert (entry["name"], entry["aliases"], entry["stale"]) == ("Doanh thu", ["DT"], False)
    assert entry["facts"]["measure"]["func"] == "sum"
    assert entry["source_run_id"] == run_id
    assert "memory.create" in audit_actions(conn)
    kind = conn.execute(
        "SELECT detail->>'kind' FROM audit_log WHERE action = 'memory.create'"
    ).fetchone()
    assert kind == ("metric",)


@pytest.mark.parametrize(
    "changes",
    [{"query_index": 0}, {"query_index": 5}, {"candidate_index": 1}, {"name": ""},
     {"aliases": ["a"] * 11}],
)  # fmt: skip
def test_save_rejects_what_the_run_did_not_compute(
    client: TestClient, conn: psycopg.Connection, key: str, changes: dict[str, Any]
) -> None:
    dataset_id = dataset(client, conn, key)
    run_id = run(client, conn, key, dataset_id, REVENUE)
    assert save(client, key, dataset_id, run_id, **changes).status_code == 422
    assert metrics(client, key, dataset_id) == []


def test_save_rejects_a_run_of_another_dataset_or_workspace(
    client: TestClient, conn: psycopg.Connection, key: str
) -> None:
    first = dataset(client, conn, key)
    run_id = run(client, conn, key, first, REVENUE)
    second = dataset(client, conn, key)
    assert save(client, key, second, run_id).status_code == 422

    other = create_key(conn, create_workspace(conn, "other"))
    assert save(client, other, first, run_id).status_code == 404


def test_save_rejects_a_run_that_has_not_finished(
    client: TestClient, conn: psycopg.Connection, key: str
) -> None:
    dataset_id = dataset(client, conn, key)
    run_id = client.post(
        "/v1/runs", headers=auth(key), json={"dataset_id": dataset_id, "question": "Revenue?"}
    ).json()["id"]
    assert save(client, key, dataset_id, run_id).status_code == 409


def test_body_cannot_carry_sql(client: TestClient, conn: psycopg.Connection, key: str) -> None:
    dataset_id = dataset(client, conn, key)
    run_id = run(client, conn, key, dataset_id, REVENUE)
    forged = {"table": "orders", "measure": "SUM(phone)", "filters": []}
    assert save(client, key, dataset_id, run_id, definition=forged).status_code == 422


def test_rename_metric(client: TestClient, conn: psycopg.Connection, key: str) -> None:
    dataset_id = dataset(client, conn, key)
    metric_id = save(client, key, dataset_id, run(client, conn, key, dataset_id, REVENUE)).json()[
        "id"
    ]
    response = client.patch(
        f"/v1/memory/{metric_id}", headers=auth(key), json={"name": "Revenue", "aliases": []}
    )
    assert response.status_code == 200
    [entry] = metrics(client, key, dataset_id)
    assert (entry["id"], entry["name"], entry["aliases"]) == (response.json()["id"], "Revenue", [])
    assert "memory.edit" in audit_actions(conn)


def test_metric_text_is_read_only_and_a_note_cannot_be_renamed(
    client: TestClient, conn: psycopg.Connection, key: str
) -> None:
    dataset_id = dataset(client, conn, key)
    metric_id = save(client, key, dataset_id, run(client, conn, key, dataset_id, REVENUE)).json()[
        "id"
    ]
    edit = client.patch(f"/v1/memory/{metric_id}", headers=auth(key), json={"text": "Other"})
    assert edit.status_code == 409
    note_id = client.post(
        f"/v1/datasets/{dataset_id}/memory",
        headers=auth(key),
        json={"kind": "correction", "text": "Rule."},
    ).json()["id"]
    rename = client.patch(f"/v1/memory/{note_id}", headers=auth(key), json={"name": "X"})
    assert rename.status_code == 409
    empty = client.patch(f"/v1/memory/{note_id}", headers=auth(key), json={})
    assert empty.status_code == 422


def test_a_metric_cannot_be_written_as_a_plain_note(
    client: TestClient, conn: psycopg.Connection, key: str
) -> None:
    dataset_id = dataset(client, conn, key)
    response = client.post(
        f"/v1/datasets/{dataset_id}/memory",
        headers=auth(key),
        json={"kind": "metric", "text": "Doanh thu"},
    )
    assert response.status_code == 422


def test_badge_only_for_metrics_in_snapshot(
    client: TestClient, conn: psycopg.Connection, key: str
) -> None:
    dataset_id = dataset(client, conn, key)
    before = run(client, conn, key, dataset_id, JUNE)
    metric_id = save(client, key, dataset_id, run(client, conn, key, dataset_id, REVENUE)).json()[
        "id"
    ]
    after = run(client, conn, key, dataset_id, JUNE)

    [old] = view(client, key, before)["explanations"]
    [new] = view(client, key, after)["explanations"]
    assert old["metrics_used"] == []
    assert new["metrics_used"] == [{"id": metric_id, "name": "Doanh thu"}]


def test_memory_list_marks_stale(client: TestClient, conn: psycopg.Connection, key: str) -> None:
    dataset_id = dataset(client, conn, key)
    save(client, key, dataset_id, run(client, conn, key, dataset_id, REVENUE))
    columns = [
        {"source": "Thành tiền", "name": "amount"},
        {"source": "Trạng thái", "name": "status"},
    ]
    renamed = {"tables": [{"source": "Don hang", "name": "orders", "columns": columns}]}
    conn.execute("UPDATE datasets SET spec = %s WHERE id = %s", (Jsonb(renamed), dataset_id))
    [entry] = metrics(client, key, dataset_id)
    assert entry["stale"] is True
