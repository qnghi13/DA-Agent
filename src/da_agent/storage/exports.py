"""Exported result files and the checks around them (docs/design/2026-11-14-result-export.md).

Exports are copies of user data, so they live for EXPORT_TTL_SECONDS and are then deleted.
"""

from pathlib import Path
from uuid import UUID

import psycopg

from da_agent.storage.csv_export import EXPORT_TTL_SECONDS

CHANGED_DATASET = "the dataset changed after this answer; ask again"


def export_file(root: Path, job_id: UUID) -> Path:
    return root / f"{job_id}.csv"


def export_filename(run_id: str, query_index: int) -> str:
    return f"da-agent-{run_id[:8]}-query-{query_index + 1}.csv"


def dataset_changed_since_run(
    conn: psycopg.Connection, workspace_id: UUID, run_id: UUID, dataset_id: UUID
) -> bool:
    """True unless the dataset is ready and unchanged since the run was asked. An edited spec
    rebuilds a dataset under the same id (ADR 0019), so its rows may differ from
    what the run saw."""
    row = conn.execute(
        "SELECT d.status = 'ready' AND d.updated_at <= j.created_at "
        "FROM datasets d JOIN jobs j ON j.workspace_id = d.workspace_id "
        "WHERE d.id = %s AND j.id = %s AND d.workspace_id = %s AND j.kind = 'run'",
        (dataset_id, run_id, workspace_id),
    ).fetchone()
    return row is None or not row[0]


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
