"""Time travel over a run's checkpoints (ADR 0042)."""

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from da_agent.agent.graph import run_graph
from da_agent.agent.loop_types import Document
from da_agent.llm.client import CostLedger, LLMClient, Route
from da_agent.storage.sqlite_dataset import SqliteDataset
from da_agent.timetravel import copy_checkpoint, list_checkpoints, show_checkpoint
from tests.unit.test_agent_graph import ScriptedAdapter, plan, sql, submit
from tests.unit.test_agent_graph import dataset as dataset


def answer(dataset: SqliteDataset, script: list, saver: InMemorySaver, thread: str):
    adapter = ScriptedAdapter(script)
    client = LLMClient([Route(adapter, "m")], CostLedger(), sleep=lambda _: None, max_retries=0)
    result = run_graph(
        "Total revenue?", as_of="2026-06-28", dataset=dataset,
        documents=[Document("rules", "Revenue excludes cancelled orders.")], llm=client,
        run_id=thread, thread_id=thread, checkpointer=saver,
    )  # fmt: skip
    return result, adapter


@pytest.fixture
def finished(dataset: SqliteDataset) -> InMemorySaver:
    saver = InMemorySaver()
    answer(dataset, [plan(), sql(), submit("number", 350)], saver, "r1")
    return saver


def test_list_shows_each_step_in_order_with_the_nodes_that_ran(finished: InMemorySaver) -> None:
    rows = list_checkpoints(finished, "r1")

    assert [row.step for row in rows] == sorted(row.step for row in rows)
    assert rows[0].ran == ["input"]
    assert "plan" in [node for row in rows for node in row.ran]
    assert rows[-1].next == [] and rows[-1].stop_reason == "answered"
    assert len({row.checkpoint_id for row in rows}) == len(rows)


def test_show_cuts_long_values(finished: InMemorySaver) -> None:
    last = list_checkpoints(finished, "r1")[-1]

    state = show_checkpoint(finished, "r1", last.checkpoint_id, max_chars=40)

    assert state["stop_reason"] == "answered"
    texts = [m["content"] for m in state["messages"] if isinstance(m.get("content"), str)]
    assert texts and all(len(text) <= 41 for text in texts)


def test_a_fork_reruns_from_a_step_on_a_new_thread_and_leaves_the_run_alone(
    dataset: SqliteDataset, finished: InMemorySaver
) -> None:
    rows = list_checkpoints(finished, "r1")
    after_plan = next(row for row in rows if "plan" in row.ran)
    latest = rows[-1].checkpoint_id

    copy_checkpoint(finished, "r1", after_plan.checkpoint_id, "fork-1")
    result, adapter = answer(dataset, [sql(), submit("number", 350)], finished, "fork-1")

    assert result.stop_reason == "answered" and result.answer is not None
    assert result.answer.value == 350
    assert len(adapter.requests) == 2  # the plan was not asked for again
    assert list_checkpoints(finished, "r1")[-1].checkpoint_id == latest


def test_a_fork_of_the_last_step_returns_the_result_without_model_calls(
    dataset: SqliteDataset, finished: InMemorySaver
) -> None:
    last = list_checkpoints(finished, "r1")[-1]

    copy_checkpoint(finished, "r1", last.checkpoint_id, "fork-2")
    result, adapter = answer(dataset, [], finished, "fork-2")

    assert result.stop_reason == "answered"
    assert adapter.requests == []


def test_an_unknown_checkpoint_is_a_clear_error(finished: InMemorySaver) -> None:
    with pytest.raises(LookupError, match="no checkpoint"):
        show_checkpoint(finished, "r1", "nope")
    with pytest.raises(LookupError, match="no checkpoint"):
        copy_checkpoint(finished, "r1", "nope", "fork-3")
    with pytest.raises(LookupError, match="no checkpoints"):
        list_checkpoints(finished, "missing")
