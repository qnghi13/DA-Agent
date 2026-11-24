"""Delete jobs: remove every row, file, and checkpoint of one dataset (see "Worker: the `delete`
job" in docs/design/2026-11-24-dataset-deletion.md).

The steps run in order: stop the dataset's other jobs, remove its files, purge old staging, delete
its checkpoint threads, then delete its rows in one transaction. Files and checkpoints go before
rows, so a worker that dies part way leaves rows that say what is left, and the retry repeats
every step harmlessly. Storage hides a dataset being deleted, so this module reads its rows with
its own SQL. `llm_calls`, `audit_log`, and the delete job's own row are kept on purpose.
"""

import shutil
import threading
import time
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
from langgraph.checkpoint.postgres import PostgresSaver

from da_agent.agent.checkpoints import checkpoint_connection, delete_run_threads
from da_agent.storage.db import connect
from da_agent.storage.exports import export_file
from da_agent.storage.jobs import Job, JobQueue
from da_agent.worker.staging import purge_staging
from da_agent.worker.worker import JobWaitError, PermanentJobError, RetryableJobError

DELETED = {"status": "deleted"}


def direct_child(root: Path, name: str | None) -> Path | None:
    """root/name when name names a direct child of root, else None. Rejects empty names, `.` and
    `..`, absolute paths, and any separator."""
    if not name or name in (".", "..") or "/" in name or "\\" in name or "\0" in name:
        return None
    base = root.resolve()
    path = base / name
    if path.parent != base or path.name != name:
        return None
    return path


def _remove(path: Path) -> None:
    """Remove a file, link, or folder tree. A link is removed, never followed; missing is fine."""
    try:
        if path.is_symlink() or not path.is_dir():
            path.unlink(missing_ok=True)
        else:
            shutil.rmtree(path)
    except FileNotFoundError:
        pass


def _owned(name: str, dataset: str) -> bool:
    """Folders named for the dataset: its id, `<id>.old` from an interrupted install, and every
    `<id>-v...` upload or build of a version."""
    return name in (dataset, f"{dataset}.old") or name.startswith(f"{dataset}-v")


class DeleteHandler:
    def __init__(
        self,
        *,
        url: str,
        schema: str | None = None,
        uploads_root: Path,
        datasets_root: Path,
        exports_root: Path,
        staging_root: Path,
        wait_seconds: float = 30.0,
        max_wait_seconds: float = 7200.0,
    ) -> None:
        self.url = url
        self.schema = schema
        self.uploads_root = uploads_root
        self.datasets_root = datasets_root
        self.exports_root = exports_root
        self.staging_root = staging_root
        # How long to wait for the dataset's running jobs before looking again.
        self.wait_seconds = wait_seconds
        # A delete older than this whose jobs still run fails: it shows in ops failures.
        self.max_wait_seconds = max_wait_seconds

    def __call__(self, job: Job, lost: threading.Event) -> dict[str, Any]:
        try:
            dataset = UUID(str(job.payload.get("dataset_id")))
        except ValueError as error:
            raise PermanentJobError("the delete job names no dataset") from error
        workspace = job.workspace_id
        with connect(self.url, schema=self.schema) as conn:
            row = conn.execute(
                "SELECT status FROM datasets WHERE id = %s AND workspace_id = %s",
                (dataset, workspace),
            ).fetchone()
            if row is None:
                # An earlier attempt deleted the rows, which is the last step.
                return DELETED
            if row[0] != "deleting":
                raise PermanentJobError("the dataset is not being deleted")
            self._stop_jobs(conn, job, dataset)
            try:
                self._remove_files(conn, workspace, dataset)
                purge_staging(self.staging_root, now=time.time())
            except OSError as error:
                # The message is shown to the workspace, so it names no path.
                raise RetryableJobError("could not remove the dataset's files") from error
            self._delete_checkpoints(conn, workspace, dataset)
            self._delete_rows(conn, job, dataset)
        return DELETED

    def _stop_jobs(self, conn: psycopg.Connection, job: Job, dataset: UUID) -> None:
        """Ask the dataset's other jobs to stop again, and wait while any still runs. Files are
        never removed under a running job."""
        queue = JobQueue(conn)
        queue.cancel_dataset_jobs(job.workspace_id, dataset, except_id=job.id)
        if not queue.running_jobs_of_dataset(dataset, except_id=job.id):
            return
        if (job.queue_wait_seconds or 0.0) > self.max_wait_seconds:
            raise PermanentJobError("other jobs of the dataset did not stop")
        raise JobWaitError(self.wait_seconds)

    def _remove_files(self, conn: psycopg.Connection, workspace: UUID, dataset: UUID) -> None:
        exports = conn.execute(
            "SELECT id FROM jobs WHERE workspace_id = %s AND kind = 'export' "
            "AND payload ->> 'dataset_id' = %s",
            (workspace, str(dataset)),
        ).fetchall()
        for (export_id,) in exports:
            path = direct_child(self.exports_root, export_file(self.exports_root, export_id).name)
            if path is not None:
                _remove(path)
        versions = conn.execute(
            "SELECT upload_dir, build_dir FROM dataset_versions WHERE dataset_id = %s",
            (dataset,),
        ).fetchall()
        for upload_dir, build_dir in versions:
            for root, name in ((self.uploads_root, upload_dir), (self.datasets_root, build_dir)):
                # A name that is not a direct child of its root is skipped, never removed.
                path = direct_child(root, name)
                if path is not None:
                    _remove(path)
        # Folders whose row says nothing: a crash between making the folder and writing the row.
        for root in (self.uploads_root, self.datasets_root):
            if not root.is_dir():
                continue
            for child in root.iterdir():
                path = direct_child(root, child.name)
                if path is not None and _owned(child.name, str(dataset)):
                    _remove(path)

    def _delete_checkpoints(self, conn: psycopg.Connection, workspace: UUID, dataset: UUID) -> int:
        runs = conn.execute(
            "SELECT id FROM jobs WHERE workspace_id = %s AND kind = 'run' "
            "AND payload ->> 'dataset_id' = %s",
            (workspace, str(dataset)),
        ).fetchall()
        if not runs:
            return 0
        with checkpoint_connection(self.url, self.schema) as cp:
            saver = PostgresSaver(cp)
            saver.setup()
            return delete_run_threads(saver, cp, [str(run_id) for (run_id,) in runs])

    def _delete_rows(self, conn: psycopg.Connection, job: Job, dataset: UUID) -> None:
        """Golden cases, the dataset's other jobs (earlier failed deletes too), then the dataset;
        cascades take versions, memory, conversations, events, attempts, and feedback."""
        workspace = job.workspace_id
        with conn.transaction():
            conn.execute(
                "DELETE FROM golden_cases WHERE workspace_id = %s AND dataset_id = %s",
                (workspace, dataset),
            )
            conn.execute(
                "DELETE FROM jobs WHERE workspace_id = %s AND payload ->> 'dataset_id' = %s "
                "AND id <> %s",
                (workspace, str(dataset), job.id),
            )
            conn.execute(
                "DELETE FROM datasets WHERE id = %s AND workspace_id = %s AND status = 'deleting'",
                (dataset, workspace),
            )
