"""Exported result files and the checks around them (docs/design/2026-11-14-result-export.md).

Exports are copies of user data, so they live for EXPORT_TTL_SECONDS and are then deleted.
"""

from pathlib import Path
from uuid import UUID

import psycopg

from da_agent.storage.csv_export import EXPORT_TTL_SECONDS
from da_agent.storage.dataset_versions import DatasetVersionStore, payload_version

CHANGED_DATASET = "the dataset changed after this answer; ask again"


def export_file(root: Path, job_id: UUID) -> Path:
    return root / f"{job_id}.csv"


def export_filename(run_id: str, query_index: int) -> str:
    return f"da-agent-{run_id[:8]}-query-{query_index + 1}.csv"


def edited_export_filename(query_id: str) -> str:
    return f"da-agent-{query_id[:8]}-edited.csv"


def job_dataset_version(conn: psycopg.Connection, workspace_id: UUID, job_id: UUID) -> int | None:
    """The data version a run, or the query edited from a run, reads. None if there is no such
    job. The answer to a question is tied to this version, so an export or an edited query reads
    the same data."""
    row = conn.execute(
        "SELECT kind, payload FROM jobs WHERE id = %s AND workspace_id = %s",
        (job_id, workspace_id),
    ).fetchone()
    if row is None or row[0] not in ("run", "query"):
        return None
    kind, payload = row
    if kind == "query":
        try:
            return job_dataset_version(conn, workspace_id, UUID(str(payload.get("run_id"))))
        except ValueError:
            return None
    return payload_version(payload)


def applied_build_dir(
    conn: psycopg.Connection, workspace_id: UUID, dataset_id: UUID, version: int
) -> str | None:
    """The folder under datasets_root of an applied version of the dataset, else None."""
    found = DatasetVersionStore(conn).get(workspace_id, dataset_id, version)
    if found is None or found.status != "applied":
        return None
    return found.build_dir


def dataset_changed_since_run(
    conn: psycopg.Connection,
    workspace_id: UUID,
    run_id: UUID,
    dataset_id: UUID,
    *,
    datasets_root: Path | None = None,
) -> bool:
    """True when the data the run (or the query edited from it) read can no longer be read as it
    was: the version is not applied, or its folder is gone from `datasets_root` (when given).
    A newer upload leaves older versions alone, so it does not count. An edited spec rebuilds
    the current version in place (ADR 0019), so for that version a dataset that is not ready or
    was rebuilt after the job was queued counts as changed."""
    version = job_dataset_version(conn, workspace_id, run_id)
    if version is None:
        return True
    row = conn.execute(
        "SELECT d.status = 'ready', d.updated_at <= j.created_at, d.current_version "
        "FROM datasets d JOIN jobs j ON j.workspace_id = d.workspace_id "
        "WHERE d.id = %s AND j.id = %s AND d.workspace_id = %s AND j.kind IN ('run', 'query')",
        (dataset_id, run_id, workspace_id),
    ).fetchone()
    if row is None:
        return True
    build_dir = applied_build_dir(conn, workspace_id, dataset_id, version)
    if build_dir is None or (
        datasets_root is not None and not (datasets_root / build_dir).is_dir()
    ):
        return True
    return version == row[2] and not (row[0] and row[1])


def is_expired(path: Path, *, now: float, ttl_seconds: int = EXPORT_TTL_SECONDS) -> bool:
    try:
        return now - path.stat().st_mtime > ttl_seconds
    except FileNotFoundError:
        return True


def purge_expired(root: Path, *, now: float, ttl_seconds: int = EXPORT_TTL_SECONDS) -> int:
    """Delete export files older than the TTL. Returns how many were deleted."""
    if not root.is_dir():
        return 0
    deleted = 0
    for path in root.glob("*.csv"):
        if is_expired(path, now=now, ttl_seconds=ttl_seconds):
            path.unlink(missing_ok=True)
            deleted += 1
    return deleted
