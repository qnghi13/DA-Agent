"""Where agent runs keep their checkpoints. See docs/adr/0001 and docs/adr/0013.

Postgres lets a run that stopped in one worker, killed or paused to ask the user, continue in
another. Tests and single evals can use LangGraph's in-memory saver instead.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.conninfo import make_conninfo
from psycopg.rows import DictRow, dict_row, tuple_row

from da_agent.storage.db import search_path_option


@contextmanager
def postgres_checkpointer(url: str) -> Iterator[PostgresSaver]:
    """Open a Postgres checkpointer, creating its tables on first use."""
    with PostgresSaver.from_conn_string(url) as saver:
        saver.setup()
        yield saver


def checkpoint_connection(url: str, schema: str | None) -> psycopg.Connection[DictRow]:
    """A connection for PostgresSaver; schema, for tests, sets the search path to one schema."""
    conninfo = make_conninfo(url, options=search_path_option(schema))
    # The settings PostgresSaver.from_conn_string uses.
    return psycopg.Connection[DictRow].connect(
        conninfo, autocommit=True, prepare_threshold=0, row_factory=dict_row
    )


def _like_literal(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# A thread whose delete stopped part way may have rows left in any of the three tables.
_THREADS_SQL = " UNION ".join(
    f"SELECT thread_id FROM {table} "  # noqa: S608  # fixed table names
    "WHERE thread_id = ANY(%(runs)s) OR thread_id LIKE ANY(%(forks)s)"
    for table in ("checkpoints", "checkpoint_blobs", "checkpoint_writes")
)


def delete_run_threads(
    saver: PostgresSaver, conn: psycopg.Connection[Any], run_ids: list[str]
) -> int:
    """Delete the checkpoint thread of each run and of its time travel forks
    (`fork-<run id>-<hex>`). Returns how many threads had rows."""
    if not run_ids:
        return 0
    forks = [f"fork-{_like_literal(run_id)}-%" for run_id in run_ids]
    with conn.cursor(row_factory=tuple_row) as cursor:
        rows = cursor.execute(_THREADS_SQL, {"runs": run_ids, "forks": forks}).fetchall()
    threads = sorted(str(row[0]) for row in rows)
    for thread in threads:
        saver.delete_thread(thread)
    return len(threads)


def checkpoint_version(saver: BaseCheckpointSaver, thread_id: str) -> tuple[bool, str | None]:
    """Whether the thread has a checkpoint, and the agent version that wrote its latest one.

    run_graph puts the version into the config metadata, which LangGraph copies into each
    checkpoint's metadata (ADR 0038). Checkpoints from before versions carry none."""
    saved = saver.get_tuple({"configurable": {"thread_id": thread_id}})
    if saved is None:
        return False, None
    version = saved.metadata.get("agent_version")
    return True, version if isinstance(version, str) else None
