"""Time travel over a run's checkpoints, for operators (ADR 0042).

The graph saves a checkpoint after every node. This module lists a run's steps, shows the state at
one of them, and copies one into a new thread, where the graph runs on from that step with the
current code, prompts, and model routes. The original run's thread and job are never written.

    uv run --env-file .env python -m da_agent.timetravel list RUN_ID
    uv run --env-file .env python -m da_agent.timetravel show RUN_ID CHECKPOINT_ID
    uv run --env-file .env python -m da_agent.timetravel fork RUN_ID CHECKPOINT_ID

It reads the worker's settings (DA_AGENT_* and provider keys). A fork calls the model, so it costs
money: its calls go to the cost ledger under the fork's id.
"""

import argparse
import json
import os
import sys
import uuid
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any

import psycopg
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.conninfo import make_conninfo
from psycopg.rows import DictRow, dict_row

from da_agent.agent.graph import build_graph
from da_agent.config import ConfigError, Settings
from da_agent.storage.db import connect, search_path_option
from da_agent.storage.jobs import JobQueue

MAX_ITEMS = 50


@dataclass(frozen=True)
class CheckpointRow:
    step: int
    checkpoint_id: str
    created_at: str | None
    # The nodes whose writes made this checkpoint ("input" for the first), and those due next.
    ran: list[str]
    next: list[str]
    iterations: int
    stop_reason: str | None


def _config(thread_id: str, checkpoint_id: str | None = None) -> dict[str, Any]:
    configurable = {"thread_id": thread_id, "checkpoint_ns": ""}
    if checkpoint_id is not None:
        configurable["checkpoint_id"] = checkpoint_id
    return {"configurable": configurable}


def list_checkpoints(saver: BaseCheckpointSaver, thread_id: str) -> list[CheckpointRow]:
    """A run's checkpoints, oldest first."""
    history = list(build_graph(saver).get_state_history(_config(thread_id)))[::-1]
    if not history:
        raise LookupError(f"no checkpoints for run {thread_id}")
    rows = []
    previous: list[str] = ["input"]
    for snapshot in history:
        values = snapshot.values or {}
        rows.append(
            CheckpointRow(
                step=int((snapshot.metadata or {}).get("step", -1)),
                checkpoint_id=snapshot.config["configurable"]["checkpoint_id"],
                created_at=snapshot.created_at,
                ran=previous,
                next=list(snapshot.next),
                iterations=int(values.get("iterations", 0)),
                stop_reason=values.get("stop_reason"),
            )
        )
        previous = list(snapshot.next) or ["end"]
    return rows


def _tuple(saver: BaseCheckpointSaver, thread_id: str, checkpoint_id: str) -> Any:
    found = saver.get_tuple(_config(thread_id, checkpoint_id))  # type: ignore[arg-type]
    if found is None:
        raise LookupError(f"no checkpoint {checkpoint_id} in run {thread_id}")
    return found


def _cut(value: Any, max_chars: int) -> Any:
    if isinstance(value, str):
        return value if len(value) <= max_chars else value[:max_chars] + "…"
    if isinstance(value, dict):
        return {key: _cut(item, max_chars) for key, item in value.items()}
    if isinstance(value, list | tuple):
        items = [_cut(item, max_chars) for item in value[:MAX_ITEMS]]
        if len(value) > MAX_ITEMS:
            items.append(f"… {len(value) - MAX_ITEMS} more")
        return items
    return value


def show_checkpoint(
    saver: BaseCheckpointSaver, thread_id: str, checkpoint_id: str, *, max_chars: int = 500
) -> dict[str, Any]:
    """The state at one checkpoint, with long strings and lists cut for reading."""
    _tuple(saver, thread_id, checkpoint_id)
    snapshot = build_graph(saver).get_state(_config(thread_id, checkpoint_id))
    return _cut(dict(snapshot.values), max_chars)


def copy_checkpoint(
    saver: BaseCheckpointSaver, thread_id: str, checkpoint_id: str, new_thread_id: str
) -> None:
    """Start new_thread_id at a copy of one checkpoint, so the graph runs on from that step
    there. The original thread is only read.

    Pending writes stay behind: the checkpointer keeps the next step's results as writes on this
    checkpoint, and copying them would replay that step instead of running it again.
    """
    found = _tuple(saver, thread_id, checkpoint_id)
    metadata = {
        **(found.metadata or {}),
        "source": "fork",
        "forked_from": f"{thread_id}/{checkpoint_id}",
    }
    checkpoint = found.checkpoint
    saver.put(
        _config(new_thread_id),  # type: ignore[arg-type]
        checkpoint,
        metadata,  # type: ignore[arg-type]
        checkpoint["channel_versions"],
    )


def _print_rows(rows: Sequence[CheckpointRow]) -> None:
    print(f"{'step':>4}  {'checkpoint':<38}  {'ran':<14}  {'next':<14}  iter  stop")
    for row in rows:
        print(
            f"{row.step:>4}  {row.checkpoint_id:<38}  {','.join(row.ran):<14}  "
            f"{','.join(row.next) or '-':<14}  {row.iterations:>4}  {row.stop_reason or '-'}"
        )


def _checkpoint_connection(settings: Settings) -> psycopg.Connection[DictRow]:
    path = search_path_option(settings.schema)
    conninfo = make_conninfo(settings.database_url, options=path) if path else settings.database_url
    return psycopg.Connection[DictRow].connect(
        conninfo, autocommit=True, prepare_threshold=0, row_factory=dict_row
    )


def _run_fork(settings: Settings, run_id: str, fork_id: str) -> dict[str, Any]:
    """Run the fork as the worker would run the job: same dataset, documents, routes, and
    limits. Imported here, since only a fork needs model routes and the sandbox."""
    from da_agent.worker.__main__ import build_worker

    with connect(settings.database_url, schema=settings.schema) as conn:
        try:
            job = JobQueue(conn).find_run(uuid.UUID(run_id))
        except ValueError as error:
            raise LookupError(f"{run_id!r} is not a run id") from error
        if job is None:
            raise LookupError(f"no run {run_id}")
        handler = build_worker(settings, os.environ, ledger_conn=conn).handlers["run"]
        return handler.fork(job, fork_id)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="List, show, or fork a run's checkpoints.")
    commands = parser.add_subparsers(dest="command", required=True)
    listing = commands.add_parser("list", help="every checkpoint of a run")
    listing.add_argument("run_id")
    listing.add_argument("--json", action="store_true")
    show = commands.add_parser("show", help="the state at one checkpoint")
    show.add_argument("run_id")
    show.add_argument("checkpoint_id")
    show.add_argument("--max-chars", type=int, default=500)
    fork = commands.add_parser("fork", help="run on from a checkpoint in a new thread")
    fork.add_argument("run_id")
    fork.add_argument("checkpoint_id")
    args = parser.parse_args(argv)

    try:
        settings = Settings.from_env(os.environ)
        with _checkpoint_connection(settings) as conn:
            saver = PostgresSaver(conn)
            if args.command == "list":
                rows = list_checkpoints(saver, args.run_id)
                if args.json:
                    print(json.dumps([asdict(row) for row in rows], indent=2, default=str))
                else:
                    _print_rows(rows)
            elif args.command == "show":
                state = show_checkpoint(
                    saver, args.run_id, args.checkpoint_id, max_chars=args.max_chars
                )
                print(json.dumps(state, indent=2, ensure_ascii=False, default=str))
            else:
                fork_id = f"fork-{uuid.uuid4()}"
                copy_checkpoint(saver, args.run_id, args.checkpoint_id, fork_id)
                print(f"fork {fork_id}", file=sys.stderr, flush=True)
                result = _run_fork(settings, args.run_id, fork_id)
                print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    except (LookupError, ValueError, ConfigError, psycopg.Error) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
