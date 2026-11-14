"""A run's answer explained in plain words over HTTP. Needs Postgres (DA_AGENT_TEST_POSTGRES_URL).

See docs/design/2026-11-15-plain-explanation.md.
"""

from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from da_agent.agent.answer import AgentAnswer, AgentResult, QueryRecord, result_to_json
from tests.integration.test_api import auth, ready_dataset
from tests.integration.test_api_exports import _complete, client, exports, key

pytestmark = pytest.mark.integration
__all__ = ["client", "exports", "key"]  # fixtures shared with the export tests

SPEC = {
    "tables": [
        {"source": "Don hang", "name": "orders",
         "columns": [{"source": "Thành tiền", "name": "total"}]}
    ]
}  # fmt: skip
QUERIES = [
    QueryRecord(sql="SELECT DISTINCT status FROM orders", columns=["status"], rows=[["done"]]),
    QueryRecord(
        sql="SELECT SUM(total) AS revenue FROM orders WHERE status = 'done'",
        columns=["revenue"],
        rows=[[350]],
    ),
]


def run_with(
    client: TestClient, conn: psycopg.Connection, key: str, result: AgentResult
) -> dict[str, Any]:
    dataset_id = ready_dataset(client, conn, key)
    conn.execute("UPDATE datasets SET spec = %s", (Jsonb(SPEC),))
    run_id = client.post(
        "/v1/runs", headers=auth(key), json={"dataset_id": dataset_id, "question": "Revenue?"}
    ).json()["id"]
    _complete(conn, lambda job: result_to_json(result) if job.kind == "run" else {})
    return client.get(f"/v1/runs/{run_id}", headers=auth(key)).json()


def test_the_answers_query_is_explained_with_the_files_names(
    client: TestClient, conn: psycopg.Connection, key: str
) -> None:
    result = AgentResult(answer=AgentAnswer("number", 350), queries=QUERIES)

    run = run_with(client, conn, key, result)

    [explanation] = run["explanations"]
    assert explanation["query_index"] == 1
    assert explanation["tables"] == ["Don hang"]
    assert explanation["measures"][0]["of"]["column"] == "Thành tiền"
    assert explanation["filters"][0]["values"] == ["done"]
    assert explanation["complete"] is True


def test_a_run_without_an_answer_has_no_explanation(
    client: TestClient, conn: psycopg.Connection, key: str
) -> None:
    result = AgentResult(stop_reason="abstained", queries=QUERIES)

    assert run_with(client, conn, key, result)["explanations"] == []
