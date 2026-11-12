"""Dataset memory and conversations (ADR 0030): people write memory, runs read only what is
approved, and nothing crosses workspaces or datasets.

Needs Postgres (DA_AGENT_TEST_POSTGRES_URL).
"""

from uuid import UUID, uuid4

import psycopg
import pytest

from da_agent.storage.datasets import DatasetStore
from da_agent.storage.jobs import JobQueue
from da_agent.storage.memory import (
    MAX_TURN_TEXT,
    ConversationStore,
    MemoryNotFoundError,
    MemoryStateError,
    MemoryStore,
    memory_snapshot,
)
from da_agent.storage.workspaces import create_workspace
from da_agent.worker.memory import MemoryLoader
from tests.integration.conftest import POSTGRES_URL

pytestmark = pytest.mark.integration


@pytest.fixture
def dataset(conn: psycopg.Connection, workspace: UUID) -> UUID:
    return DatasetStore(conn).create(workspace, "Shop", "")


def test_a_users_own_correction_is_approved_at_once(
    conn: psycopg.Connection, workspace: UUID, dataset: UUID
) -> None:
    store = MemoryStore(conn)
    entry_id = store.write(
        workspace, dataset, "correction", "Revenue excludes cancelled orders.", author="key:a"
    )
    [entry] = store.approved(workspace, dataset)
    assert entry.id == entry_id
    assert entry.status == "approved"
    assert entry.text == "Revenue excludes cancelled orders."
    assert entry.author == "key:a"


def test_a_suggestion_has_no_effect_until_approved(
    conn: psycopg.Connection, workspace: UUID, dataset: UUID
) -> None:
    store = MemoryStore(conn)
    entry_id = store.suggest(workspace, dataset, "alias", "DT means revenue.", author="agent")
    assert store.approved(workspace, dataset) == []
    assert [e.status for e in store.entries(workspace, dataset)] == ["proposed"]

    store.approve(workspace, entry_id)
    assert [e.id for e in store.approved(workspace, dataset)] == [entry_id]


def test_a_rejected_suggestion_stays_out_and_cannot_be_approved_later(
    conn: psycopg.Connection, workspace: UUID, dataset: UUID
) -> None:
    store = MemoryStore(conn)
    entry_id = store.suggest(workspace, dataset, "alias", "DT means debt.", author="agent")
    store.reject(workspace, entry_id)
    assert store.approved(workspace, dataset) == []
    with pytest.raises(MemoryStateError):
        store.approve(workspace, entry_id)


def test_an_edit_replaces_the_entry_and_keeps_the_old_one_for_history(
    conn: psycopg.Connection, workspace: UUID, dataset: UUID
) -> None:
    store = MemoryStore(conn)
    old = store.write(workspace, dataset, "correction", "Revenue excludes VAT.", author="key:a")
    new = store.edit(workspace, old, "Revenue includes VAT.", author="key:b")

    [entry] = store.approved(workspace, dataset)
    assert (entry.id, entry.text, entry.author) == (new, "Revenue includes VAT.", "key:b")
    history = {e.id: e for e in store.entries(workspace, dataset)}
    assert history[old].superseded_by == new
    with pytest.raises(MemoryStateError):
        store.edit(workspace, old, "Again.", author="key:a")


def test_examples_keep_their_sql(conn: psycopg.Connection, workspace: UUID, dataset: UUID) -> None:
    store = MemoryStore(conn)
    store.write(
        workspace, dataset, "example", "Revenue in October 2025",
        sql="SELECT sum(total) FROM orders", author="key:a",
    )  # fmt: skip
    [entry] = store.approved(workspace, dataset)
    assert entry.sql == "SELECT sum(total) FROM orders"


def test_invalid_entries_are_refused(
    conn: psycopg.Connection, workspace: UUID, dataset: UUID
) -> None:
    store = MemoryStore(conn)
    with pytest.raises(MemoryStateError):
        store.write(workspace, dataset, "secret", "x", author="key:a")
    with pytest.raises(MemoryStateError):
        store.write(workspace, dataset, "correction", "   ", author="key:a")
    with pytest.raises(MemoryStateError):
        store.write(workspace, dataset, "example", "Revenue", author="key:a")  # no sql


def test_a_deleted_entry_is_gone(conn: psycopg.Connection, workspace: UUID, dataset: UUID) -> None:
    store = MemoryStore(conn)
    entry_id = store.write(workspace, dataset, "correction", "Rule.", author="key:a")
    store.delete(workspace, entry_id)
    assert store.entries(workspace, dataset) == []
    with pytest.raises(MemoryNotFoundError):
        store.delete(workspace, entry_id)


@pytest.mark.security
def test_memory_never_crosses_workspaces(
    conn: psycopg.Connection, workspace: UUID, dataset: UUID
) -> None:
    store = MemoryStore(conn)
    other = create_workspace(conn, "other")
    entry_id = store.suggest(workspace, dataset, "correction", "Rule.", author="agent")

    with pytest.raises(MemoryNotFoundError):
        store.write(other, dataset, "correction", "Planted.", author="key:x")
    for change in (store.approve, store.reject, store.delete):
        with pytest.raises(MemoryNotFoundError):
            change(other, entry_id)
    with pytest.raises(MemoryNotFoundError):
        store.edit(other, entry_id, "Planted.", author="key:x")
    assert store.entries(other, dataset) == []
    assert [e.status for e in store.entries(workspace, dataset)] == ["proposed"]


def test_memory_belongs_to_one_dataset(
    conn: psycopg.Connection, workspace: UUID, dataset: UUID
) -> None:
    store = MemoryStore(conn)
    second = DatasetStore(conn).create(workspace, "Second shop", "")
    store.write(workspace, dataset, "correction", "Rule.", author="key:a")
    assert store.approved(workspace, second) == []


def test_a_snapshot_reads_the_same_entries_after_an_edit(
    conn: psycopg.Connection, workspace: UUID, dataset: UUID
) -> None:
    store = MemoryStore(conn)
    first = store.write(workspace, dataset, "correction", "Rule one.", author="key:a")
    ids = [e.id for e in store.approved(workspace, dataset)]
    store.edit(workspace, first, "Rule two.", author="key:a")

    assert [e.text for e in store.by_ids(workspace, dataset, ids)] == ["Rule one."]
    assert store.by_ids(workspace, dataset, [uuid4()]) == []


def _finished_run(
    conn: psycopg.Connection,
    workspace: UUID,
    dataset: UUID,
    conversation: UUID | None,
    question: str,
    answer: dict | None,
) -> UUID:
    queue = JobQueue(conn)
    payload = {"dataset_id": str(dataset), "question": question, "as_of": "2025-11-01"}
    if conversation is not None:
        payload["conversation_id"] = str(conversation)
    job_id = queue.enqueue(workspace, "run", payload)
    result = {
        "answer": answer,
        "stop_reason": "answered" if answer else "abstained",
        "queries": [
            {
                "sql": "SELECT sum(total) FROM orders WHERE month = 10",
                "columns": ["sum"],
                "rows": [[152]],
                "truncated": False,
            }
        ],
    }
    conn.execute(
        "UPDATE jobs SET status = 'succeeded', result = %s::jsonb WHERE id = %s",
        (psycopg.types.json.Jsonb(result), job_id),
    )
    return job_id


def test_recent_turns_come_from_finished_runs_of_the_conversation_in_order(
    conn: psycopg.Connection, workspace: UUID, dataset: UUID
) -> None:
    conversations = ConversationStore(conn)
    chat = conversations.create(workspace, dataset)
    other_chat = conversations.create(workspace, dataset)
    _finished_run(
        conn, workspace, dataset, chat, "Revenue in October?", {"kind": "number", "value": 152}
    )
    _finished_run(
        conn, workspace, dataset, other_chat, "Unrelated?", {"kind": "number", "value": 1}
    )
    _finished_run(conn, workspace, dataset, chat, "Top product?", {"kind": "text", "value": "Tea"})
    current = _finished_run(conn, workspace, dataset, chat, "And September?", None)

    turns = conversations.recent_turns(workspace, chat, before=current, limit=5)
    assert [t.question for t in turns] == ["Revenue in October?", "Top product?"]
    assert turns[0].answer == "152"
    assert turns[0].sql == "SELECT sum(total) FROM orders WHERE month = 10"
    assert conversations.recent_turns(workspace, chat, before=current, limit=1)[0].question == (
        "Top product?"
    )


def test_turns_are_bounded(conn: psycopg.Connection, workspace: UUID, dataset: UUID) -> None:
    conversations = ConversationStore(conn)
    chat = conversations.create(workspace, dataset)
    _finished_run(conn, workspace, dataset, chat, "q" * 5000, {"kind": "text", "value": "a" * 5000})
    current = _finished_run(conn, workspace, dataset, chat, "next", None)
    [turn] = conversations.recent_turns(workspace, chat, before=current)
    assert len(turn.question) <= MAX_TURN_TEXT
    assert len(turn.answer) <= MAX_TURN_TEXT


@pytest.mark.security
def test_conversations_never_cross_workspaces(
    conn: psycopg.Connection, workspace: UUID, dataset: UUID
) -> None:
    conversations = ConversationStore(conn)
    chat = conversations.create(workspace, dataset)
    current = _finished_run(conn, workspace, dataset, chat, "q", None)
    other = create_workspace(conn, "other")
    assert conversations.get(other, chat) is None
    with pytest.raises(MemoryNotFoundError):
        conversations.recent_turns(other, chat, before=current)
    with pytest.raises(MemoryNotFoundError):
        conversations.create(other, dataset)


def _job(conn: psycopg.Connection, workspace: UUID, job_id: UUID):
    found = JobQueue(conn).get(job_id, workspace)
    assert found is not None
    return found


def test_a_run_reads_the_memory_it_was_created_with_and_its_earlier_turns(
    schema: str, conn: psycopg.Connection, workspace: UUID, dataset: UUID
) -> None:
    store = MemoryStore(conn)
    store.write(workspace, dataset, "correction", "Revenue excludes cancelled orders.", author="k")
    chat = ConversationStore(conn).create(workspace, dataset)
    _finished_run(
        conn, workspace, dataset, chat, "Revenue in October?", {"kind": "number", "value": 152}
    )
    snapshot = memory_snapshot(conn, workspace, dataset)
    payload = {
        "dataset_id": str(dataset),
        "question": "And September?",
        "as_of": "2025-11-01",
        "conversation_id": str(chat),
        **snapshot,
    }
    job_id = JobQueue(conn).enqueue(workspace, "run", payload)
    store.write(workspace, dataset, "correction", "Written after the run started.", author="k")

    assert POSTGRES_URL is not None
    documents = MemoryLoader(url=POSTGRES_URL, schema=schema)(_job(conn, workspace, job_id))
    text = "\n".join(d.text for d in documents)
    assert "Revenue excludes cancelled orders." in text
    assert "Written after the run started." not in text
    assert "Revenue in October?" in text and "152" in text


def test_a_run_without_memory_or_conversation_gets_no_documents(
    schema: str, conn: psycopg.Connection, workspace: UUID, dataset: UUID
) -> None:
    payload = {"dataset_id": str(dataset), "question": "q", "as_of": "2025-11-01"}
    job_id = JobQueue(conn).enqueue(workspace, "run", payload)
    assert POSTGRES_URL is not None
    assert MemoryLoader(url=POSTGRES_URL, schema=schema)(_job(conn, workspace, job_id)) == []
