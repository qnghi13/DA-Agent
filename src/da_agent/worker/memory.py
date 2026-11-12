"""Loads a run's memory as documents (ADR 0030).

The API records the ids of the approved notes in the run's payload when it creates the run
(memory_snapshot in storage.memory), and the worker reads exactly those, so a resumed run sees
what it started with even if someone edits memory meanwhile. Earlier turns are the
conversation's finished runs created before this one.
"""

from uuid import UUID

from da_agent.agent.loop_types import Document
from da_agent.agent.memory import EarlierTurn, MemoryNote, memory_documents
from da_agent.storage.db import connect
from da_agent.storage.jobs import Job
from da_agent.storage.memory import ConversationStore, MemoryNotFoundError, MemoryStore


class MemoryLoader:
    def __init__(self, *, url: str, schema: str | None = None) -> None:
        self.url = url
        self.schema = schema

    def __call__(self, job: Job) -> list[Document]:
        payload = job.payload
        ids = [UUID(value) for value in payload.get("memory_ids") or []]
        conversation = payload.get("conversation_id")
        if not ids and not conversation:
            return []
        dataset_id = UUID(payload["dataset_id"])
        with connect(self.url, schema=self.schema) as conn:
            entries = MemoryStore(conn).by_ids(job.workspace_id, dataset_id, ids)
            turns = []
            if conversation:
                try:
                    turns = ConversationStore(conn).recent_turns(
                        job.workspace_id, UUID(conversation), before=job.id
                    )
                except MemoryNotFoundError:
                    turns = []  # deleted with its dataset, or never in this workspace
        notes = [MemoryNote(e.kind, e.text, e.sql, e.author) for e in entries]
        return memory_documents(notes, [EarlierTurn(t.question, t.answer, t.sql) for t in turns])
