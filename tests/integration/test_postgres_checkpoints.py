"""Durable runs on the Postgres checkpointer: a run killed in one worker continues in another.

Needs a Postgres server. Set DA_AGENT_TEST_POSTGRES_URL, for example
postgresql://da_agent:da_agent_dev@127.0.0.1:5433/da_agent, or the tests are skipped.
"""

import os
import sqlite3
import uuid
from pathlib import Path

import pytest

from da_agent.agent.checkpoints import postgres_checkpointer
from da_agent.agent.graph import run_graph
from da_agent.agent.loop_types import Document
from da_agent.llm.client import CostLedger, LLMClient, Route
from da_agent.storage.sqlite_dataset import SqliteDataset
from tests.unit.test_agent_graph import ScriptedAdapter, WorkerKilled, plan, sql, submit

URL = os.environ.get("DA_AGENT_TEST_POSTGRES_URL")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not URL, reason="DA_AGENT_TEST_POSTGRES_URL is not set"),
]


@pytest.fixture
def dataset(tmp_path: Path) -> SqliteDataset:
    path = tmp_path / "shop.sqlite"
    connection = sqlite3.connect(path)
    connection.executescript(
        "CREATE TABLE orders (id INTEGER, total INTEGER);"
        "INSERT INTO orders VALUES (1, 100); INSERT INTO orders VALUES (2, 250);"
    )
    connection.commit()
    connection.close()
    return SqliteDataset(path)


def run_in_worker(dataset: SqliteDataset, adapter: ScriptedAdapter, run_id: str):
    """A fresh connection and client for each call, as a separate worker process would have."""
    client = LLMClient([Route(adapter, "m")], CostLedger(), sleep=lambda _: None, max_retries=0)
    assert URL is not None
    with postgres_checkpointer(URL) as saver:
        return run_graph(
            "Total revenue?",
            as_of="2026-06-28",
            dataset=dataset,
            documents=[Document("rules", "Revenue is the sum of totals.")],
            llm=client,
            run_id=run_id,
            checkpointer=saver,
        )


def test_a_run_killed_in_one_worker_finishes_in_another(dataset: SqliteDataset) -> None:
    run_id = f"test-{uuid.uuid4()}"
    adapter = ScriptedAdapter([plan(), sql(), WorkerKilled(), submit("number", 350)])

    with pytest.raises(WorkerKilled):
        run_in_worker(dataset, adapter, run_id)

    result = run_in_worker(dataset, adapter, run_id)

    assert result.stop_reason == "answered"
    assert result.answer is not None and result.answer.value == 350
    assert len(adapter.requests) == 4
    assert [step.sql for step in result.steps if step.sql] == ["SELECT SUM(total) AS t FROM orders"]


def test_a_finished_run_is_read_back_without_model_calls(dataset: SqliteDataset) -> None:
    run_id = f"test-{uuid.uuid4()}"
    adapter = ScriptedAdapter([plan(), sql(), submit("number", 350)])
    run_in_worker(dataset, adapter, run_id)

    again = run_in_worker(dataset, adapter, run_id)

    assert again.stop_reason == "answered"
    assert len(adapter.requests) == 3
