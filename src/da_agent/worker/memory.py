"""Loads a run's memory as documents (ADR 0030).

The API records the ids of the approved notes in the run's payload when it creates the run
(memory_snapshot in storage.memory), and the worker reads exactly those, so a resumed run sees
what it started with even if someone edits memory meanwhile. Earlier turns are the
conversation's finished runs created before this one.

A metric whose table or columns the current dataset no longer has (a rebuild renamed or dropped
them) is stale: it is left out, so the model never reads a definition that cannot run.
"""

import logging
from uuid import UUID

from da_agent.agent.loop_types import Document
from da_agent.agent.memory import EarlierTurn, MemoryNote, memory_documents
from da_agent.reports.metric import Definition, is_stale
from da_agent.storage.db import connect
from da_agent.storage.jobs import Job
from da_agent.storage.memory import (
    ConversationStore,
    MemoryEntry,
    MemoryNotFoundError,
    MemoryStore,
)

logger = logging.getLogger(__name__)


def _note(entry: MemoryEntry, visible: dict[str, set[str]]) -> MemoryNote | None:
    if entry.kind != "metric":
        return MemoryNote(entry.kind, entry.text, entry.sql, entry.author)
    if entry.definition is None:
        return None
    definition = Definition.from_json(entry.definition)
    if is_stale(definition, visible):
        logger.warning("metric %r (%s) is stale and was left out", entry.name, entry.id)
        return None
    return MemoryNote(
        entry.kind, entry.text, None, entry.author, name=entry.name,
        aliases=tuple(entry.aliases or ()), definition=definition,
    )  # fmt: skip


class MemoryLoader:
    def __init__(self, *, url: str, schema: str | None = None) -> None:
        self.url = url
        self.schema = schema

    def __call__(self, job: Job, visible: dict[str, set[str]]) -> list[Document]:
        """The run's memory. `visible` maps each table of the dataset to its visible columns."""
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
        notes = [note for e in entries if (note := _note(e, visible)) is not None]
        return memory_documents(notes, [EarlierTurn(t.question, t.answer, t.sql) for t in turns])
