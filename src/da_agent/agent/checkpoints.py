"""Where agent runs keep their checkpoints. See docs/adr/0001 and docs/adr/0013.

Postgres lets a run that stopped in one worker, killed or paused to ask the user, continue in
another. Tests and single evals can use LangGraph's in-memory saver instead.
"""

from collections.abc import Iterator
from contextlib import contextmanager

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.postgres import PostgresSaver


@contextmanager
def postgres_checkpointer(url: str) -> Iterator[PostgresSaver]:
    """Open a Postgres checkpointer, creating its tables on first use."""
    with PostgresSaver.from_conn_string(url) as saver:
        saver.setup()
        yield saver


def checkpoint_version(saver: BaseCheckpointSaver, thread_id: str) -> tuple[bool, str | None]:
    """Whether the thread has a checkpoint, and the agent version that wrote its latest one.

    run_graph puts the version into the config metadata, which LangGraph copies into each
    checkpoint's metadata (ADR 0038). Checkpoints from before versions carry none."""
    saved = saver.get_tuple({"configurable": {"thread_id": thread_id}})
    if saved is None:
        return False, None
    version = saved.metadata.get("agent_version")
    return True, version if isinstance(version, str) else None
