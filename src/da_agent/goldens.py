"""Golden cases: rerun the answers users confirmed, with today's code (ADR 0043).

A thumbs-up on an answered run makes it a golden case. This operator tool lists a workspace's cases
and the runs users rated down, and reruns the cases: each one answers its question again, in this
process, with the worker's dataset, documents, memory, routes, and limits, and is compared with
the answer the user confirmed.

    uv run --env-file .env python -m da_agent.goldens list --workspace W
    uv run --env-file .env python -m da_agent.goldens run --workspace W [--dataset D] --yes

A rerun calls the model, so `run` first says how many cases it would run and spends nothing
without --yes. "changed" means look again: the user may have confirmed a wrong answer.
"""

import argparse
import datetime as dt
import json
import os
import sys
import uuid
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

import psycopg

from da_agent.agent.compare import compare_answers
from da_agent.config import ConfigError, Settings
from da_agent.storage.db import connect
from da_agent.storage.feedback import GoldenCase, disliked_runs, golden_cases
from da_agent.storage.jobs import JobQueue
from da_agent.worker.runs import RunHandler


def rerun_cases(
    conn: psycopg.Connection,
    cases: Sequence[GoldenCase],
    handler: RunHandler,
    on_row: Callable[[dict[str, Any]], None] | None = None,
) -> list[dict[str, Any]]:
    """Rerun each case and compare. A case that fails is reported as "error"; the rest go on.
    on_row gets each row as soon as its case finishes, so a stopped rerun keeps what it paid for."""
    rows: list[dict[str, Any]] = []

    def done(finished: dict[str, Any]) -> None:
        rows.append(finished)
        if on_row is not None:
            on_row(finished)

    for case in cases:
        # Named after the source run, so the ledger charges the rerun to its workspace.
        run_id = f"golden-{case.source_run_id}-{uuid.uuid4().hex[:8]}"
        row: dict[str, Any] = {
            "case_id": str(case.id), "source_run_id": str(case.source_run_id), "run_id": run_id,
            "question": case.question, "expected": case.expected,
        }  # fmt: skip
        try:
            job = JobQueue(conn).find_run(case.source_run_id)
            if job is None:
                raise LookupError("the run this case came from is gone")
            result = handler.rerun(job, run_id)
        except Exception as error:  # One broken case must not stop the others.
            done({**row, "outcome": "error", "actual": None, "error": str(error)[:500]})
            continue
        actual = result.get("answer")
        # An answer the run did not stand behind (unverified, stopped) is no answer.
        answered = result.get("stop_reason") == "answered"
        outcome = compare_answers(case.expected, actual if answered else None)
        done(
            {**row, "outcome": outcome, "actual": actual, "stop_reason": result.get("stop_reason")}
        )
    return rows


def _positive(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return value


def _worker_handler(settings: Settings, conn: psycopg.Connection) -> RunHandler:
    from da_agent.worker.__main__ import build_worker

    return build_worker(settings, os.environ, ledger_conn=conn).handlers["run"]


def _cases_view(cases: Sequence[GoldenCase]) -> list[dict[str, Any]]:
    return [{**asdict(case), "id": str(case.id), "dataset_id": str(case.dataset_id),
             "source_run_id": str(case.source_run_id)} for case in cases]  # fmt: skip


def main(
    argv: Sequence[str] | None = None,
    *,
    handler_factory: Callable[[], RunHandler] | None = None,
) -> int:
    parser = argparse.ArgumentParser(description="List or rerun a workspace's golden cases.")
    commands = parser.add_subparsers(dest="command", required=True)
    for name, text in (("list", "the cases and the runs rated down"), ("run", "rerun the cases")):
        command = commands.add_parser(name, help=text)
        command.add_argument("--workspace", required=True, type=uuid.UUID)
        command.add_argument("--dataset", type=uuid.UUID)
    commands.choices["list"].add_argument("--json", action="store_true")
    run = commands.choices["run"]
    run.add_argument("--limit", type=_positive)
    run.add_argument("--yes", action="store_true", help="rerun them; this calls the model")
    run.add_argument(
        "--output", type=Path, help="JSONL report; default DATA_DIR/goldens/goldens-<time>.jsonl"
    )
    args = parser.parse_args(argv)

    try:
        settings = Settings.from_env(os.environ)
        with connect(settings.database_url, schema=settings.schema) as conn:
            cases = golden_cases(conn, args.workspace, args.dataset)
            if args.command == "list":
                disliked = disliked_runs(conn, args.workspace, limit=50, dataset_id=args.dataset)
                if args.json:
                    shown = {"cases": _cases_view(cases), "disliked": disliked}
                    print(json.dumps(shown, indent=2, ensure_ascii=False, default=str))
                else:
                    for case in cases:
                        print(f"case {case.id}  {case.question}  -> {json.dumps(case.expected)}")
                    for row in disliked:
                        reasons = ",".join(row["reasons"]) or "-"
                        print(f"down {row['run_id']}  {row['question']}  {reasons}")
                        if row["note"]:
                            print(f"     note: {row['note']}")
                return 0
            if args.limit is not None:
                cases = cases[: args.limit]
            print(f"{len(cases)} golden case{'s' if len(cases) != 1 else ''} to rerun")
            if not args.yes:
                print("each one calls the model; add --yes to run them")
                return 0
            # The report holds users' questions and answers, so by default it goes with their
            # other data, outside the repository. It is opened before anything is spent.
            stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
            output = args.output or settings.data_dir / "goldens" / f"goldens-{stamp}.jsonl"
            if args.output is None:
                output.parent.mkdir(parents=True, exist_ok=True)
            with output.open("x", encoding="utf-8") as report:
                handler = handler_factory() if handler_factory else _worker_handler(settings, conn)

                def write(row: dict[str, Any]) -> None:
                    report.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
                    report.flush()
                    print(f"{row['outcome']:<9} {row['question']}", flush=True)

                rows = rerun_cases(conn, cases, handler, on_row=write)
    except (ConfigError, ValueError, LookupError, OSError, psycopg.Error) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    counts = Counter(row["outcome"] for row in rows)
    print(", ".join(f"{name} {counts[name]}" for name in ("same", "changed", "no_answer", "error")))
    print(f"report: {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
