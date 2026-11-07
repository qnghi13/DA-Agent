"""Datasets and their review state. Every read and change is scoped to a workspace.

The ingest job proposes a spec and installs a build. The user approves it as is, which makes the
dataset ready at once, or edits it, which queues a build job in the same transaction. See
docs/adr/0019.
"""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import class_row
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from da_agent.ingestion.spec import DatasetSpec
from da_agent.storage.db import returned_id
from da_agent.storage.jobs import JobQueue

MAX_SPEC_ERRORS = 5


class DatasetStateError(Exception):
    """The dataset is missing for this workspace, or not in a state that allows the change.
    The message is safe to show to the workspace."""


class DatasetNotFoundError(DatasetStateError):
    """No such dataset in this workspace."""


class InvalidSpecError(DatasetStateError):
    """An edited spec failed validation."""


@dataclass(frozen=True)
class DatasetRecord:
    id: UUID
    workspace_id: UUID
    name: str
    description: str
    status: str
    spec: dict[str, Any] | None
    summary: dict[str, Any]
    documents: list[dict[str, str]]
    error: str | None
    files: list[dict[str, Any]]


def normalized_spec(spec: dict[str, Any]) -> dict[str, Any]:
    """Validate a spec and return it in stored form, or raise DatasetStateError."""
    try:
        return DatasetSpec.model_validate(spec).model_dump(mode="json")
    except ValidationError as error:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}"
            for e in error.errors()[:MAX_SPEC_ERRORS]
        )
        raise InvalidSpecError(f"the edited spec is invalid: {problems}") from error


class DatasetStore:
    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def create(
        self,
        workspace_id: UUID,
        name: str,
        description: str,
        *,
        dataset_id: UUID | None = None,
        files: list[dict[str, Any]] | None = None,
    ) -> UUID:
        return returned_id(
            self._conn.execute(
                "INSERT INTO datasets (id, workspace_id, name, description, files) "
                "VALUES (coalesce(%s, gen_random_uuid()), %s, %s, %s, %s) RETURNING id",
                (dataset_id, workspace_id, name, description, Jsonb(files or [])),
            )
        )

    def in_workspace(self, workspace_id: UUID) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT id, name, status, created_at FROM datasets WHERE workspace_id = %s "
            "ORDER BY created_at DESC LIMIT 200",
            (workspace_id,),
        ).fetchall()
        return [
            {"id": str(r[0]), "name": r[1], "status": r[2], "created_at": r[3].isoformat()}
            for r in rows
        ]

    def get(self, dataset_id: UUID, workspace_id: UUID) -> DatasetRecord | None:
        with self._conn.cursor(row_factory=class_row(DatasetRecord)) as cursor:
            return cursor.execute(
                "SELECT id, workspace_id, name, description, status, spec, summary, documents, "
                "error, files FROM datasets WHERE id = %s AND workspace_id = %s",
                (dataset_id, workspace_id),
            ).fetchone()

    def mark_proposed(
        self,
        dataset_id: UUID,
        spec: dict[str, Any],
        summary: dict[str, Any],
        documents: list[dict[str, str]],
    ) -> None:
        self._conn.execute(
            "UPDATE datasets SET status = 'proposed', spec = %s, summary = %s, documents = %s, "
            "error = NULL, updated_at = now() WHERE id = %s",
            (Jsonb(spec), Jsonb(summary), Jsonb(documents), dataset_id),
        )

    def mark_ready(self, dataset_id: UUID, summary: dict[str, Any]) -> None:
        self._conn.execute(
            "UPDATE datasets SET status = 'ready', summary = %s, error = NULL, updated_at = now() "
            "WHERE id = %s",
            (Jsonb(summary), dataset_id),
        )

    def mark_failed(self, dataset_id: UUID, error: str) -> None:
        self._conn.execute(
            "UPDATE datasets SET status = 'failed', error = %s, updated_at = now() WHERE id = %s",
            (error, dataset_id),
        )

    def approve(self, workspace_id: UUID, dataset_id: UUID, spec: dict[str, Any] | None) -> str:
        """Approve the proposed spec (spec None or unchanged), or queue a build of an edited one.
        Returns the new status: 'ready' or 'building'."""
        with self._conn.transaction():
            row = self._conn.execute(
                "SELECT status, spec FROM datasets WHERE id = %s AND workspace_id = %s FOR UPDATE",
                (dataset_id, workspace_id),
            ).fetchone()
            if row is None:
                raise DatasetNotFoundError("dataset not found")
            status, proposed = row
            if status != "proposed":
                raise DatasetStateError(
                    f"the dataset is {status}; only a proposed dataset can be approved"
                )
            edited = normalized_spec(spec) if spec is not None else proposed
            if edited == proposed:
                self._conn.execute(
                    "UPDATE datasets SET status = 'ready', updated_at = now() WHERE id = %s",
                    (dataset_id,),
                )
                return "ready"
            self._conn.execute(
                "UPDATE datasets SET status = 'building', spec = %s, updated_at = now() "
                "WHERE id = %s",
                (Jsonb(edited), dataset_id),
            )
            JobQueue(self._conn).enqueue(workspace_id, "build", {"dataset_id": str(dataset_id)})
            return "building"
