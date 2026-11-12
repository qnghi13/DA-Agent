"""Dataset memory and conversations (ADR 0030). Every read and change is scoped to a workspace.

People write memory through the API: an entry a user writes is approved at once, and one the
agent suggests stays proposed until someone approves it. A run reads only approved entries that no
edit has replaced. An edit adds a new entry and points the old one at it, so the history stays and
a run that recorded the old ids reads the same text when it resumes.

Conversations link runs; a run reads the last few finished turns of its conversation, bounded.
"""

import json
from dataclasses import dataclass
from typing import Any, Literal, get_args
from uuid import UUID

import psycopg
from psycopg.rows import class_row

from da_agent.storage.db import returned_id

MemoryKind = Literal["correction", "alias", "example"]
KINDS = frozenset(get_args(MemoryKind))
MAX_TEXT = 2000
MAX_SQL = 10000
# Bounds each field of a conversation turn, because turns go into every later prompt.
MAX_TURN_TEXT = 500
MAX_TURN_SQL = 1000
DEFAULT_TURNS = 3
MAX_APPROVED = 200


class MemoryStateError(Exception):
    """The entry is invalid, or not in a state that allows the change.
    The message is safe to show to the workspace."""


class MemoryNotFoundError(MemoryStateError):
    """No such entry, dataset, or conversation in this workspace."""


@dataclass(frozen=True)
class MemoryEntry:
    id: UUID
    dataset_id: UUID
    kind: str
    text: str
    sql: str | None
    status: str
    author: str
    source_run_id: UUID | None
    superseded_by: UUID | None


@dataclass(frozen=True)
class Turn:
    """One earlier question in a conversation and what the run answered, bounded."""

    question: str
    answer: str
    sql: str | None


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


class MemoryStore:
    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def _require_dataset(self, workspace_id: UUID, dataset_id: UUID) -> None:
        row = self._conn.execute(
            "SELECT 1 FROM datasets WHERE id = %s AND workspace_id = %s",
            (dataset_id, workspace_id),
        ).fetchone()
        if row is None:
            raise MemoryNotFoundError("dataset not found")

    def _insert(
        self,
        workspace_id: UUID,
        dataset_id: UUID,
        kind: str,
        text: str,
        *,
        sql: str | None,
        author: str,
        status: str,
        source_run_id: UUID | None,
    ) -> UUID:
        text = text.strip()
        sql = sql.strip() if sql and sql.strip() else None
        if kind not in KINDS:
            raise MemoryStateError(f"kind must be one of {', '.join(sorted(KINDS))}")
        if not text or len(text) > MAX_TEXT:
            raise MemoryStateError(f"text must have 1 to {MAX_TEXT} characters")
        if sql is not None and len(sql) > MAX_SQL:
            raise MemoryStateError(f"sql must have at most {MAX_SQL} characters")
        if kind == "example" and sql is None:
            raise MemoryStateError("an example needs the SQL that answered it")
        self._require_dataset(workspace_id, dataset_id)
        return returned_id(
            self._conn.execute(
                "INSERT INTO dataset_memories (workspace_id, dataset_id, kind, text, sql, status, "
                "author, source_run_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (workspace_id, dataset_id, kind, text, sql, status, author, source_run_id),
            )
        )

    def write(
        self,
        workspace_id: UUID,
        dataset_id: UUID,
        kind: str,
        text: str,
        *,
        author: str,
        sql: str | None = None,
        source_run_id: UUID | None = None,
    ) -> UUID:
        """An entry a user writes themselves: approved at once."""
        return self._insert(
            workspace_id, dataset_id, kind, text, sql=sql, author=author, status="approved",
            source_run_id=source_run_id,
        )  # fmt: skip

    def suggest(
        self,
        workspace_id: UUID,
        dataset_id: UUID,
        kind: str,
        text: str,
        *,
        author: str,
        sql: str | None = None,
        source_run_id: UUID | None = None,
    ) -> UUID:
        """An entry suggested for review: no run reads it until someone approves it."""
        return self._insert(
            workspace_id, dataset_id, kind, text, sql=sql, author=author, status="proposed",
            source_run_id=source_run_id,
        )  # fmt: skip

    def _get(self, workspace_id: UUID, entry_id: UUID, *, lock: bool = False) -> MemoryEntry:
        with self._conn.cursor(row_factory=class_row(MemoryEntry)) as cursor:
            entry = cursor.execute(
                "SELECT id, dataset_id, kind, text, sql, status, author, source_run_id, "
                "superseded_by "
                "FROM dataset_memories WHERE id = %s AND workspace_id = %s"
                + (" FOR UPDATE" if lock else ""),
                (entry_id, workspace_id),
            ).fetchone()
        if entry is None:
            raise MemoryNotFoundError("memory entry not found")
        return entry

    def _review(self, workspace_id: UUID, entry_id: UUID, status: str) -> None:
        with self._conn.transaction():
            entry = self._get(workspace_id, entry_id, lock=True)
            if entry.status != "proposed":
                raise MemoryStateError(
                    f"the entry is {entry.status}; only a proposed entry can be reviewed"
                )
            self._conn.execute(
                "UPDATE dataset_memories SET status = %s, updated_at = now() WHERE id = %s",
                (status, entry_id),
            )

    def approve(self, workspace_id: UUID, entry_id: UUID) -> None:
        self._review(workspace_id, entry_id, "approved")

    def reject(self, workspace_id: UUID, entry_id: UUID) -> None:
        self._review(workspace_id, entry_id, "rejected")

    def edit(
        self, workspace_id: UUID, entry_id: UUID, text: str, *, author: str,
        sql: str | None = None,
    ) -> UUID:  # fmt: skip
        """Replace an entry with an approved one written by author. Returns the new id."""
        with self._conn.transaction():
            old = self._get(workspace_id, entry_id, lock=True)
            if old.superseded_by is not None or old.status == "rejected":
                raise MemoryStateError("only a current, not rejected entry can be edited")
            new = self._insert(
                workspace_id, old.dataset_id, old.kind, text,
                sql=sql if sql is not None else old.sql, author=author, status="approved",
                source_run_id=old.source_run_id,
            )  # fmt: skip
            self._conn.execute(
                "UPDATE dataset_memories SET superseded_by = %s, updated_at = now() WHERE id = %s",
                (new, entry_id),
            )
            return new

    def delete(self, workspace_id: UUID, entry_id: UUID) -> None:
        deleted = self._conn.execute(
            "DELETE FROM dataset_memories WHERE id = %s AND workspace_id = %s RETURNING id",
            (entry_id, workspace_id),
        ).fetchone()
        if deleted is None:
            raise MemoryNotFoundError("memory entry not found")

    def entries(self, workspace_id: UUID, dataset_id: UUID) -> list[MemoryEntry]:
        """Every entry of the dataset, oldest first, for review."""
        with self._conn.cursor(row_factory=class_row(MemoryEntry)) as cursor:
            return cursor.execute(
                "SELECT id, dataset_id, kind, text, sql, status, author, source_run_id, "
                "superseded_by "
                "FROM dataset_memories "
                "WHERE dataset_id = %s AND workspace_id = %s ORDER BY created_at, id",
                (dataset_id, workspace_id),
            ).fetchall()

    def approved(self, workspace_id: UUID, dataset_id: UUID) -> list[MemoryEntry]:
        """The entries a run reads: approved and not replaced, oldest first."""
        with self._conn.cursor(row_factory=class_row(MemoryEntry)) as cursor:
            return cursor.execute(
                "SELECT id, dataset_id, kind, text, sql, status, author, source_run_id, "
                "superseded_by "
                "FROM dataset_memories "
                "WHERE dataset_id = %s AND workspace_id = %s AND status = 'approved' "
                "AND superseded_by IS NULL ORDER BY created_at, id LIMIT %s",
                (dataset_id, workspace_id, MAX_APPROVED),
            ).fetchall()

    def by_ids(self, workspace_id: UUID, dataset_id: UUID, ids: list[UUID]) -> list[MemoryEntry]:
        """The entries a run recorded when it started, for a resume. Deleted ones are gone."""
        if not ids:
            return []
        with self._conn.cursor(row_factory=class_row(MemoryEntry)) as cursor:
            return cursor.execute(
                "SELECT id, dataset_id, kind, text, sql, status, author, source_run_id, "
                "superseded_by "
                "FROM dataset_memories "
                "WHERE id = ANY(%s) AND dataset_id = %s AND workspace_id = %s "
                "ORDER BY created_at, id",
                (list(ids), dataset_id, workspace_id),
            ).fetchall()


def memory_snapshot(
    conn: psycopg.Connection, workspace_id: UUID, dataset_id: UUID
) -> dict[str, Any]:
    """The run payload field that fixes which notes a new run reads, even after edits. Empty
    when the dataset has none, so such a run's payload stays as it was before memory."""
    entries = MemoryStore(conn).approved(workspace_id, dataset_id)
    return {"memory_ids": [str(entry.id) for entry in entries]} if entries else {}


def _answer_text(result: dict[str, Any] | None) -> str:
    answer = (result or {}).get("answer")
    if not answer:
        return f"(no answer: {(result or {}).get('stop_reason', 'unknown')})"
    value = answer.get("value")
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    reason = answer.get("reason")
    return f"{text} ({reason})" if reason and answer.get("kind") == "unanswerable" else text


def _last_sql(result: dict[str, Any] | None) -> str | None:
    queries = (result or {}).get("queries") or []
    return queries[-1].get("sql") if queries else None


class ConversationStore:
    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def create(self, workspace_id: UUID, dataset_id: UUID) -> UUID:
        MemoryStore(self._conn)._require_dataset(workspace_id, dataset_id)
        return returned_id(
            self._conn.execute(
                "INSERT INTO conversations (workspace_id, dataset_id) VALUES (%s, %s) RETURNING id",
                (workspace_id, dataset_id),
            )
        )

    def get(self, workspace_id: UUID, conversation_id: UUID) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT id, dataset_id, created_at FROM conversations "
            "WHERE id = %s AND workspace_id = %s",
            (conversation_id, workspace_id),
        ).fetchone()
        if row is None:
            return None
        return {"id": row[0], "dataset_id": row[1], "created_at": row[2]}

    def recent_turns(
        self, workspace_id: UUID, conversation_id: UUID, *, before: UUID,
        limit: int = DEFAULT_TURNS,
    ) -> list[Turn]:  # fmt: skip
        """The last finished runs of the conversation created before the run `before`, oldest
        first. Runs still open or failed are left out."""
        if self.get(workspace_id, conversation_id) is None:
            raise MemoryNotFoundError("conversation not found")
        rows = self._conn.execute(
            """
            SELECT payload->>'question', result FROM jobs
            WHERE conversation_id = %s AND workspace_id = %s AND kind = 'run'
              AND status = 'succeeded' AND id <> %s
              AND created_at <= (SELECT created_at FROM jobs WHERE id = %s)
            ORDER BY created_at DESC, id DESC LIMIT %s
            """,
            (conversation_id, workspace_id, before, before, limit),
        ).fetchall()
        turns = []
        for question, result in reversed(rows):
            sql = _last_sql(result)
            turns.append(
                Turn(
                    question=_clip(question or "", MAX_TURN_TEXT),
                    answer=_clip(_answer_text(result), MAX_TURN_TEXT),
                    sql=_clip(sql, MAX_TURN_SQL) if sql else None,
                )
            )
        return turns
