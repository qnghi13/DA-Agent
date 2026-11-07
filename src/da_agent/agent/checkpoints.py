"""Where agent runs keep their checkpoints. See docs/adr/0001 and docs/adr/0013.

Postgres lets a run that stopped in one worker, killed or paused to ask the user, continue in
another. Tests and single evals can use LangGraph's in-memory saver instead.
"""

from collections.abc import Iterator
from contextlib import contextmanager

from langgraph.checkpoint.postgres import PostgresSaver


@contextmanager
def postgres_checkpointer(url: str) -> Iterator[PostgresSaver]:
    """Open a Postgres checkpointer, creating its tables on first use."""
    with PostgresSaver.from_conn_string(url) as saver:
        saver.setup()
        yield saver
