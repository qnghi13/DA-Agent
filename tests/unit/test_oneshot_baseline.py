"""The one-shot text-to-SQL baseline: one query from the schema and documents, then an answer.

It measures what the agent adds over the plain approach, so it must stay plain: no exploration,
no retry after a failed query, no verification.
"""

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from da_agent.agent.loop_types import AgentLimits, Document
from da_agent.llm.client import CostLedger, LLMClient, Route, RunBudget
from da_agent.llm.types import (
    InvalidToolCallError,
    LLMRequest,
    LLMResponse,
    PermanentLLMError,
    ToolCall,
    Usage,
)
from da_agent.storage.sqlite_dataset import SqliteDataset
from eval.baselines.oneshot import run_oneshot


class ScriptedAdapter:
    provider = "fake"

    def __init__(self, script: list) -> None:
        self.script = list(script)
        self.requests: list[LLMRequest] = []

    def complete(self, request: LLMRequest, model: str) -> LLMResponse:
        self.requests.append(request.model_copy(deep=True))
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


def call(name: str, arguments: dict[str, Any]) -> LLMResponse:
    tool_call = ToolCall(id=f"c_{name}", name=name, arguments=arguments)
    return LLMResponse(tool_calls=[tool_call], usage=Usage(input_tokens=10, output_tokens=5))


def sql(query: str) -> LLMResponse:
    return call("run_sql", {"sql": query})


def submit(kind: str, value: Any = None) -> LLMResponse:
    arguments: dict[str, Any] = {"kind": kind}
    if value is not None:
        arguments["value_json"] = json.dumps(value)
    return call("submit_answer", arguments)


def text(content: str) -> LLMResponse:
    return LLMResponse(content=content, usage=Usage(input_tokens=10, output_tokens=5))


@pytest.fixture
def dataset(tmp_path: Path) -> SqliteDataset:
    path = tmp_path / "shop.sqlite"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE orders (id INTEGER, total INTEGER, status TEXT);
        INSERT INTO orders VALUES (1, 100, 'paid'), (2, 250, 'paid'), (3, 40, 'cancelled');
        """
    )
    connection.commit()
    connection.close()
    return SqliteDataset(path)


DOCUMENTS = [
    Document("rulebook.md", "Answers are plain numbers."),
    Document("topics.md", "## 1. Revenue\nRevenue excludes cancelled orders.", on_demand=True),
]


def run(dataset: SqliteDataset, script: list, **limits: Any):
    adapter = ScriptedAdapter(script)
    client = LLMClient([Route(adapter, "m")], CostLedger(), sleep=lambda _: None, max_retries=0)
    result = run_oneshot(
        "Total revenue?",
        as_of="2026-06-28",
        dataset=dataset,
        documents=DOCUMENTS,
        llm=client,
        run_id="r1",
        limits=AgentLimits(**limits),
    )
    return result, adapter


def tool_names(request: LLMRequest) -> list[str]:
    return [tool.name for tool in request.tools]


def test_one_query_then_an_answer_from_its_result(dataset: SqliteDataset) -> None:
    query = "SELECT SUM(total) AS revenue FROM orders WHERE status <> 'cancelled'"

    result, adapter = run(dataset, [sql(query), submit("number", 350)])

    assert result.stop_reason == "answered"
    assert result.answer is not None and result.answer.value == 350
    assert [(step.tool, step.ok, step.sql) for step in result.steps] == [
        ("run_sql", True, query),
        ("submit_answer", True, None),
    ]
    assert result.iterations == 2
    # The model writes the query with no other tool, then answers with nothing but submit_answer.
    assert [tool_names(r) for r in adapter.requests] == [["run_sql"], ["submit_answer"]]
    answer_turn = adapter.requests[1].messages
    assert any(m.role == "tool" and "350" in (m.content or "") for m in answer_turn)


def test_the_query_turn_sees_the_schema_every_document_and_the_date(
    dataset: SqliteDataset,
) -> None:
    _, adapter = run(dataset, [sql("SELECT 1 AS n"), submit("number", 1)])

    prompt = "\n".join(m.content or "" for m in adapter.requests[0].messages)
    assert "Total revenue?" in prompt
    assert "2026-06-28" in prompt
    assert "orders" in prompt and "status" in prompt
    # Rules a tool would fetch are in the prompt in full: the baseline has no search_rules.
    assert "Revenue excludes cancelled orders." in prompt
    assert "Answers are plain numbers." in prompt


def test_a_failed_query_is_not_retried(dataset: SqliteDataset) -> None:
    result, adapter = run(dataset, [sql("SELECT nope FROM orders"), submit("unanswerable")])

    assert len(adapter.requests) == 2
    assert tool_names(adapter.requests[1]) == ["submit_answer"]
    assert not result.steps[0].ok
    assert result.answer is not None and result.answer.kind == "unanswerable"


@pytest.mark.parametrize(
    ("script", "reason"),
    [
        ([text("The revenue is 350.")], "no_tool_call"),
        ([sql("SELECT 1 AS n"), text("It is 1.")], "no_tool_call"),
        ([InvalidToolCallError("bad call")], "invalid_tool_call"),
        ([sql("SELECT 1 AS n"), call("submit_answer", {"kind": "weather"})], "answer_format"),
    ],
)
def test_a_turn_without_a_usable_call_ends_the_run_unanswered(
    dataset: SqliteDataset, script: list, reason: str
) -> None:
    result, adapter = run(dataset, script)

    assert result.answer is None
    assert result.stop_reason == reason
    # No nudge and no second chance: the agent's repairs are what the baseline leaves out.
    assert len(adapter.requests) == len(script)


def test_provider_failures_stay_retryable_and_budgets_stop(dataset: SqliteDataset) -> None:
    failed, _ = run(dataset, [PermanentLLMError("down")])
    over, _ = run(
        dataset,
        [sql("SELECT 1 AS n"), submit("number", 1)],
        budget=RunBudget(max_total_tokens=10),
    )

    assert failed.stop_reason == "llm_error"
    assert failed.error is not None and "down" in failed.error
    assert over.stop_reason == "budget_exceeded"
    assert over.answer is None
