"""Dataset versions: each upload of more files is a new version.

Design: docs/design/2026-11-21-add-files.md.

A version moves uploaded -> checking -> (applied | needs_choice | proposed | failed | discarded);
needs_choice and proposed lead to building -> applied. At most one version per dataset is open,
enforced by a unique partial index. Every read and change is scoped to a workspace, except the
worker-side methods (`mark`, `apply`, `current`), which take the dataset id from a job payload.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

import psycopg
from psycopg import errors
from psycopg.rows import class_row
from psycopg.types.json import Jsonb

from da_agent.storage.datasets import DatasetNotFoundError, DatasetStateError
from da_agent.storage.jobs import JobQueue

STATUSES = frozenset(
    {
        "uploaded", "checking", "needs_choice", "proposed", "building",
        "applied", "failed", "discarded",
    }
)  # fmt: skip
_JSON_FIELDS = frozenset(
    {
        "files",
        "spec",
        "summary",
        "documents",
        "tables",
        "changes",
        "choices",
        "spec_edits",
        "repairs",
    }
)
CHANGED_WHILE_UPLOADING = "the data changed while you uploaded; try again"
_TEXT_FIELDS = frozenset({"upload_dir", "build_dir", "error"})
_COLUMNS = (
    "dataset_id, version, status, files, upload_dir, build_dir, spec, summary, documents, "
    "tables, changes, choices, spec_edits, repairs, error, created_at, updated_at"
)


@dataclass(frozen=True)
class VersionRecord:
    dataset_id: UUID
    version: int
    status: str
    files: list[dict[str, Any]]
    upload_dir: str
    build_dir: str | None
    spec: dict[str, Any] | None
    summary: dict[str, Any] | None
    documents: list[dict[str, str]] | None
    tables: Any
    changes: Any
    choices: dict[str, Any] | None
    spec_edits: dict[str, Any] | None
    repairs: Any
    error: str | None
    created_at: Any
    updated_at: Any


def payload_version(payload: dict[str, Any]) -> int:
    """The data version a run, query, or export job reads. Payloads from before versions existed
    have none and mean version 1."""
    value = payload.get("dataset_version", 1)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 1 else 1


def ensure_first_version(conn: psycopg.Connection, dataset_id: UUID) -> None:
    """Give a ready dataset its version 1 and make it current, if it has none yet.

    Called when a dataset becomes ready by the normal upload path, so every ready dataset has a
    current version. Must run in the caller's transaction. Folders keep the dataset id."""
    conn.execute(
        "INSERT INTO dataset_versions "
        "(dataset_id, version, status, files, upload_dir, build_dir, spec, summary, documents) "
        "SELECT id, 1, 'applied', files, id::text, id::text, spec, summary, documents "
        "FROM datasets WHERE id = %s ON CONFLICT DO NOTHING",
        (dataset_id,),
    )
    conn.execute(
        "UPDATE datasets SET current_version = coalesce(current_version, 1) WHERE id = %s",
        (dataset_id,),
    )


class DatasetVersionStore:
    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def _fetch(self, where: str, params: tuple[Any, ...]) -> list[VersionRecord]:
        with self._conn.cursor(row_factory=class_row(VersionRecord)) as cursor:
            return cursor.execute(
                # Columns are a constant and `where` is built in this module, never from a caller.
                f"SELECT {_COLUMNS} FROM dataset_versions v WHERE {where} ORDER BY version",  # noqa: S608  # pyright: ignore[reportArgumentType]
                params,
            ).fetchall()

    def open_version(
        self,
        workspace_id: UUID,
        dataset_id: UUID,
        files: list[dict[str, Any]],
        upload_dir: str,
        max_versions: int,
        *,
        version: int | None = None,
        base_version: int | None = None,
    ) -> int:
        """Insert the next version as `uploaded` and enqueue its check, in one transaction.
        Raises DatasetStateError if the dataset is not ready, a version is open, or the limit
        is reached. The limit counts the versions that keep a build: applied and open ones.

        Under the dataset's row lock, so a concurrent apply cannot slip in between:
        - `version` is the number the caller wrote into `files` (`added_in`);
        - `base_version` is the current version whose files the caller linked into the upload.
        When either no longer holds, nothing is inserted and DatasetStateError is raised."""
        try:
            with self._conn.transaction():
                row = self._conn.execute(
                    "SELECT status, current_version FROM datasets "
                    "WHERE id = %s AND workspace_id = %s FOR UPDATE",
                    (dataset_id, workspace_id),
                ).fetchone()
                if row is None or row[0] == "deleting":
                    raise DatasetNotFoundError("dataset not found")
                if row[0] != "ready":
                    raise DatasetStateError(
                        f"the dataset is {row[0]}; only a ready one takes files"
                    )
                if base_version is not None and row[1] != base_version:
                    raise DatasetStateError(CHANGED_WHILE_UPLOADING)
                last, kept = self._conn.execute(
                    "SELECT coalesce(max(version), 0), "
                    "count(*) FILTER (WHERE status NOT IN ('failed', 'discarded')) "
                    "FROM dataset_versions WHERE dataset_id = %s",
                    (dataset_id,),
                ).fetchone() or (0, 0)
                number = last + 1
                if version is not None and number != version:
                    raise DatasetStateError(CHANGED_WHILE_UPLOADING)
                version = number
                if kept + 1 > max_versions:
                    raise DatasetStateError(
                        f"the dataset already has {max_versions} versions; the limit is reached"
                    )
                self._conn.execute(
                    "INSERT INTO dataset_versions (dataset_id, version, files, upload_dir) "
                    "VALUES (%s, %s, %s, %s)",
                    (dataset_id, version, Jsonb(files), upload_dir),
                )
                JobQueue(self._conn).enqueue(
                    workspace_id,
                    "version",
                    {"dataset_id": str(dataset_id), "version": version, "step": "check"},
                )
        except errors.UniqueViolation as error:
            raise DatasetStateError("a version is already open") from error
        return number

    def get(self, workspace_id: UUID, dataset_id: UUID, version: int) -> VersionRecord | None:
        found = self._fetch(
            "v.dataset_id = %s AND v.version = %s AND EXISTS "
            "(SELECT 1 FROM datasets d WHERE d.id = v.dataset_id AND d.workspace_id = %s "
            "AND d.status <> 'deleting')",
            (dataset_id, version, workspace_id),
        )
        return found[0] if found else None

    def list(self, workspace_id: UUID, dataset_id: UUID) -> list[VersionRecord]:
        return self._fetch(
            "v.dataset_id = %s AND EXISTS "
            "(SELECT 1 FROM datasets d WHERE d.id = v.dataset_id AND d.workspace_id = %s "
            "AND d.status <> 'deleting')",
            (dataset_id, workspace_id),
        )

    def current(self, dataset_id: UUID) -> VersionRecord:
        found = self._fetch(
            "v.dataset_id = %s AND v.version = "
            "(SELECT current_version FROM datasets WHERE id = %s)",
            (dataset_id, dataset_id),
        )
        if not found:
            raise DatasetStateError("the dataset has no current version")
        return found[0]

    def mark(
        self,
        dataset_id: UUID,
        version: int,
        status: str,
        *,
        expected: tuple[str, ...] | None = None,
        **fields: Any,
    ) -> bool:
        """Set a version's status and any of the known columns. Unknown names raise ValueError.

        With `expected`, only a version in one of those statuses changes, so a worker never
        overwrites a version the user discarded. Returns whether a row changed."""
        unknown_statuses = ({status} | set(expected or ())) - STATUSES
        if unknown_statuses:
            raise ValueError(f"unknown status: {sorted(unknown_statuses)}")
        unknown = set(fields) - _JSON_FIELDS - _TEXT_FIELDS
        if unknown:
            raise ValueError(f"unknown version fields: {sorted(unknown)}")
        sets = ["status = %s", "updated_at = now()"]
        params: list[Any] = [status]
        for name, value in fields.items():  # names are checked against the whitelist above
            sets.append(f"{name} = %s")
            params.append(Jsonb(value) if name in _JSON_FIELDS and value is not None else value)
        where = (
            "dataset_id = %s AND version = %s AND NOT EXISTS "
            "(SELECT 1 FROM datasets d WHERE d.id = dataset_id AND d.status = 'deleting')"
        )
        params += [dataset_id, version]
        if expected is not None:
            where += " AND status = ANY(%s)"
            params.append(list(expected))
        query = f"UPDATE dataset_versions SET {', '.join(sets)} WHERE {where}"  # noqa: S608
        return self._conn.execute(query, params).rowcount > 0  # type: ignore[arg-type]

    def apply(
        self,
        dataset_id: UUID,
        version: int,
        build_dir: str,
        *,
        expected: tuple[str, ...] = ("checking", "building"),
    ) -> bool:
        """Make a version the dataset's current one, in one transaction. Idempotent.

        Only a version in one of the `expected` statuses (the worker step's own) is applied, so a
        late attempt never applies a version another attempt failed or the user discarded.
        Returns True when the version is applied, now or before."""
        with self._conn.transaction():
            deleting = self._conn.execute(
                "SELECT 1 FROM datasets WHERE id = %s AND status = 'deleting'", (dataset_id,)
            ).fetchone()
            if deleting is not None:
                return False
            row = self._conn.execute(
                "SELECT status FROM dataset_versions WHERE dataset_id = %s AND version = %s "
                "FOR UPDATE",
                (dataset_id, version),
            ).fetchone()
            if row is None:
                raise DatasetStateError("version not found")
            if row[0] == "applied":
                return True
            if row[0] not in expected:
                return False
            self._conn.execute(
                "UPDATE datasets d SET spec = v.spec, summary = coalesce(v.summary, '{}'), "
                "documents = coalesce(v.documents, '[]'), files = v.files, "
                "current_version = v.version, updated_at = now() "
                "FROM dataset_versions v WHERE d.id = v.dataset_id AND v.dataset_id = %s "
                "AND v.version = %s",
                (dataset_id, version),
            )
            self._conn.execute(
                "UPDATE dataset_versions SET status = 'applied', build_dir = %s, error = NULL, "
                "updated_at = now() WHERE dataset_id = %s AND version = %s",
                (build_dir, dataset_id, version),
            )
        return True

    def approve(
        self,
        workspace_id: UUID,
        dataset_id: UUID,
        version: int,
        choices: dict[str, Any],
        spec_edits: dict[str, Any] | None,
        accepted_repairs: list[dict[str, Any]],
    ) -> str:
        """Store the user's decisions and enqueue the build. Returns 'building'.

        `choices` is stored as given, in `choices`; `spec_edits` goes in its own column and
        `accepted_repairs` in `repairs`. Contents are validated by the API, not here. Allowed only
        from needs_choice or proposed."""
        with self._conn.transaction():
            row = self._conn.execute(
                "SELECT v.status FROM dataset_versions v JOIN datasets d ON d.id = v.dataset_id "
                "WHERE v.dataset_id = %s AND v.version = %s AND d.workspace_id = %s "
                "AND d.status <> 'deleting' FOR UPDATE OF v",
                (dataset_id, version, workspace_id),
            ).fetchone()
            if row is None:
                raise DatasetNotFoundError("version not found")
            if row[0] not in ("needs_choice", "proposed"):
                raise DatasetStateError(
                    f"the version is {row[0]}; only one that needs a decision can be approved"
                )
            self._conn.execute(
                "UPDATE dataset_versions SET status = 'building', choices = %s, spec_edits = %s, "
                "repairs = %s, updated_at = now() WHERE dataset_id = %s AND version = %s",
                (
                    Jsonb(choices),
                    Jsonb(spec_edits) if spec_edits is not None else None,
                    Jsonb(accepted_repairs),
                    dataset_id,
                    version,
                ),
            )
            JobQueue(self._conn).enqueue(
                workspace_id,
                "version",
                {"dataset_id": str(dataset_id), "version": version, "step": "build"},
            )
        return "building"

    def discard(self, workspace_id: UUID, dataset_id: UUID, version: int) -> None:
        """Drop an open version that waits for the user, or one left in a worker step whose
        latest job has ended (a worker killed on its last attempt): nothing would move it on,
        and an open version blocks every later upload. Raises DatasetStateError otherwise."""
        with self._conn.transaction():
            row = self._conn.execute(
                "SELECT v.status FROM dataset_versions v JOIN datasets d ON d.id = v.dataset_id "
                "WHERE v.dataset_id = %s AND v.version = %s AND d.workspace_id = %s "
                "AND d.status <> 'deleting' FOR UPDATE OF v",
                (dataset_id, version, workspace_id),
            ).fetchone()
            if row is None:
                raise DatasetNotFoundError("version not found")
            if row[0] in ("checking", "building"):
                if not self.stuck(workspace_id, dataset_id, version):
                    raise DatasetStateError(
                        f"the version is still {row[0]}; it can be discarded when that ends"
                    )
            elif row[0] not in ("uploaded", "needs_choice", "proposed"):
                raise DatasetStateError(f"the version is {row[0]}; it cannot be discarded")
            self._conn.execute(
                "UPDATE dataset_versions SET status = 'discarded', updated_at = now() "
                "WHERE dataset_id = %s AND version = %s",
                (dataset_id, version),
            )

    def stuck(self, workspace_id: UUID, dataset_id: UUID, version: int) -> bool:
        """Whether a version in a worker step has no job left to move it on: its latest
        `version` job has ended. Such a version can be discarded."""
        row = self._conn.execute(
            "SELECT status FROM jobs WHERE workspace_id = %s AND kind = 'version' "
            "AND payload ->> 'dataset_id' = %s AND payload ->> 'version' = %s "
            "ORDER BY created_at DESC, id DESC LIMIT 1",
            (workspace_id, str(dataset_id), str(version)),
        ).fetchone()
        return row is None or row[0] not in ("queued", "running")
