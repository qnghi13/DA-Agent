"""Run the agent over a question set. Example, from the repo root:

uv run --env-file .env python -m eval.datamate.run --data-dir train --set bootstrap --limit 3 \
    --routes groq:MODEL,gemini:MODEL --output-dir eval/reports/first --max-total-tokens 200000 \
    --datasets-dir eval/reports/datasets-dev

With a local model server, --workers runs several questions at the same time (docs/eval/gpu.md).
--all-questions runs every question of another split, such as public_test, without scoring.

With --datasets-dir, every shop is a prepared clean dataset and SQL runs in the sandbox (ADR 0011).
--agent graph runs the LangGraph workflow (ADR 0013); --checkpoint-url keeps its checkpoints in
Postgres, so a killed run continues each unfinished question where it stopped.

The run never reads answers.jsonl. Scoring happens afterwards in a separate scorer process.
"""

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from langgraph.checkpoint.base import BaseCheckpointSaver

from da_agent.agent.checkpoints import postgres_checkpointer
from da_agent.agent.graph import run_graph
from da_agent.agent.loop import AgentLimits, run_agent
from da_agent.llm.client import CostLedger, LLMClient, RunBudget
from da_agent.llm.factory import build_routes
from da_agent.reports.html import render_report
from da_agent.runner.runner import Runner, RunnerConfig
from da_agent.telemetry.tracing import configure_tracing
from eval.datamate.adapter import (
    ShopContext,
    UnsupportedShopError,
    open_prepared_shop,
    open_shop,
    to_submission,
)
from eval.scorer.scoring import load_jsonl
from eval.splits.make_splits import HOLDOUT_SET, holdout_question_ids

DEFAULT_SPLITS = Path("eval/splits/splits.json")
MAX_ERROR_CHARS = 500


@dataclass(frozen=True)
class RunConfig:
    output_dir: Path
    max_iterations: int = 10
    max_tokens_per_question: int | None = None
    max_total_tokens: int | None = None
    delay_seconds: float = 0.0
    max_consecutive_failures: int = 3
    agent: Literal["loop", "graph"] = "loop"
    # Offer make_chart and write an HTML report per question to output_dir/reports. Changes the
    # tools the model sees, so a run with reports is not comparable with the baselines.
    reports: bool = False
    # Questions that run at the same time. Above one needs prepared datasets.
    workers: int = 1


def thread_id(output_dir: Path, question_id: str) -> str:
    """Saved graph runs are per eval run, so a new run never reads an old run's answer."""
    return f"{output_dir.resolve()}::{question_id}"


def select_questions(
    data_dir: Path,
    splits_path: Path,
    question_set: str,
    limit: int | None = None,
    shops: list[str] | None = None,
    *,
    holdout: bool = False,
) -> list[dict[str, Any]]:
    """Return the questions in a set, optionally only for some shops. Refuses any set that
    touches a holdout shop. The holdout set itself runs once, at the end, and only when the caller
    passes holdout=True."""
    splits = json.loads(splits_path.read_text(encoding="utf-8"))
    questions_path = data_dir / "questions.jsonl"
    if question_set == HOLDOUT_SET:
        if not holdout:
            raise ValueError("the holdout set runs once, at the end: pass --holdout to run it")
        wanted = set(holdout_question_ids(splits_path, questions_path))
        return _filter(
            [row for row in load_jsonl(questions_path) if row["id"] in wanted], limit, shops
        )
    if question_set not in splits["question_sets"]:
        raise ValueError(f"unknown question set {question_set!r}")
    wanted = set(splits["question_sets"][question_set])
    held_out = set(splits["holdout_shops"])
    questions = [row for row in load_jsonl(questions_path) if row["id"] in wanted]
    touched = sorted({row["business_id"] for row in questions} & held_out)
    if touched:
        raise ValueError(f"question set {question_set!r} touches holdout shops: {touched}")
    return _filter(questions, limit, shops)


def all_questions(
    data_dir: Path,
    splits_path: Path,
    limit: int | None = None,
    shops: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Every question in another split's directory, such as a public test, which the training
    splits file does not list. Refuses a directory that holds training holdout shops."""
    held_out = set(json.loads(splits_path.read_text(encoding="utf-8"))["holdout_shops"])
    questions = load_jsonl(data_dir / "questions.jsonl")
    touched = sorted({row["business_id"] for row in questions} & held_out)
    if touched:
        raise ValueError(f"{data_dir} holds training holdout shops: {touched}")
    return _filter(questions, limit, shops)


def _filter(
    questions: list[dict[str, Any]], limit: int | None, shops: list[str] | None
) -> list[dict[str, Any]]:
    if shops is not None:
        questions = [row for row in questions if row["business_id"] in set(shops)]
    return questions[:limit] if limit is not None else questions


def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(row, ensure_ascii=False) + "\n")


def _build_metrics(
    results: list[dict[str, Any]],
    skipped: list[str],
    failed: dict[str, str],
    failed_tokens: int,
    stopped: str | None,
) -> dict[str, Any]:
    latencies = [row["latency_seconds"] for row in results]
    calls = sum(row["llm_calls"] for row in results)
    return {
        "attempted": len(results),
        "stop_reasons": dict(Counter(row["stop_reason"] for row in results)),
        "skipped_unsupported": skipped,
        "failed": list(failed),
        "failure_details": failed,
        "stopped": stopped,
        "llm_calls": calls,
        "input_tokens": sum(row["input_tokens"] for row in results),
        "output_tokens": sum(row["output_tokens"] for row in results),
        "total_tokens": sum(row["total_tokens"] for row in results) + failed_tokens,
        "cost_usd": sum(row["cost_usd"] for row in results),
        "mean_latency_seconds": sum(latencies) / len(latencies) if latencies else 0,
        "failed_tools": sum(row["failed_tools"] for row in results),
        **_guard_metrics(results),
    }


def _guard_metrics(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Guard signals by kind. A run is repaired when verify sent its answer back, and saved when
    a later pass in the same run then let an answer through."""
    logs = [row.get("verify_log", []) for row in results]
    repaired = [log for log in logs if any(entry != "passed" for entry in log)]
    return {
        "tool_error_kinds": dict(
            Counter(kind for row in results for kind in row.get("error_kinds", []))
        ),
        "verify_kinds": dict(Counter(e for log in logs for e in log if e != "passed")),
        "runs_repaired": len(repaired),
        "runs_saved_by_repair": sum(1 for log in repaired if log[-1] == "passed"),
    }


def run_questions(
    questions: list[dict[str, Any]],
    data_dir: Path,
    llm: LLMClient,
    ledger: CostLedger,
    config: RunConfig,
    sleep: Callable[[float], None] = time.sleep,
    open_shop_fn: Callable[[str], ShopContext] | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
) -> dict[str, Any]:
    """Run questions, saving after each one so that a later run can resume.

    open_shop_fn opens a shop by id. By default it opens the shop's pos.sqlite in this process.
    With the graph agent and a checkpointer, a question cut off by a killed run continues where it
    stopped. With config.workers above one, that many questions run at the same time, which a
    local model server needs to be used well; limits are checked before each question starts, so
    questions already running may pass them."""
    if config.reports and config.agent != "graph":
        raise ValueError("reports need the graph agent (--agent graph)")
    if config.workers > 1 and open_shop_fn is None:
        raise ValueError("workers above one need prepared datasets (--datasets-dir)")
    opener = open_shop_fn or (lambda shop_id: open_shop(data_dir, shop_id))
    config.output_dir.mkdir(parents=True, exist_ok=True)
    run = _Run(questions, llm, ledger, config, sleep, opener, checkpointer)
    started = time.monotonic()
    if config.workers == 1:
        run.work()
    else:
        with ThreadPoolExecutor(max_workers=config.workers) as pool:
            for future in [pool.submit(run.work) for _ in range(config.workers)]:
                future.result()
    wall_seconds = time.monotonic() - started

    if run.shops and not any(run.shops.values()):
        # Every shop failed to open, usually a wrong --datasets-dir. Scoring would report all
        # abstentions as if the agent had run.
        raise ValueError(f"no shop could be opened: {sorted(run.shops)}")
    metrics = _build_metrics(run.results, run.skipped, run.failed, run.failed_tokens, run.stopped)
    metrics["workers"] = config.workers
    metrics["wall_seconds"] = wall_seconds
    (config.output_dir / "metrics.json").write_text(
        json.dumps(metrics, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return metrics


class _Run:
    """The state one run shares between its workers, guarded by one lock."""

    def __init__(
        self,
        questions: list[dict[str, Any]],
        llm: LLMClient,
        ledger: CostLedger,
        config: RunConfig,
        sleep: Callable[[float], None],
        opener: Callable[[str], ShopContext],
        checkpointer: BaseCheckpointSaver | None,
    ) -> None:
        self.llm, self.ledger, self.config = llm, ledger, config
        self.sleep, self.opener, self.checkpointer = sleep, opener, checkpointer
        self.submission_path = config.output_dir / "submission.jsonl"
        self.results_path = config.output_dir / "results.jsonl"
        self.results: list[dict[str, Any]] = (
            load_jsonl(self.results_path) if self.results_path.exists() else []
        )
        done = {row["id"] for row in self.results}
        self.pending = iter([q for q in questions if q["id"] not in done])
        budget = RunBudget(max_total_tokens=config.max_tokens_per_question)
        self.limits = AgentLimits(max_iterations=config.max_iterations, budget=budget)
        self.shops: dict[str, ShopContext | None] = {}
        self.skipped: list[str] = []
        self.failed: dict[str, str] = {}
        self.failed_tokens = 0
        self.stopped: str | None = None
        self.consecutive_failures = 0
        self.lock = threading.Lock()

    def work(self) -> None:
        while (task := self._next()) is not None:
            self._run_one(*task)

    def _next(self) -> tuple[dict[str, Any], ShopContext] | None:
        """The next question to run and its shop, or None when the run is over or stopped."""
        with self.lock:
            while self.stopped is None:
                question = next(self.pending, None)
                if question is None:
                    return None
                shop_id = question["business_id"]
                if shop_id not in self.shops:
                    try:
                        self.shops[shop_id] = self.opener(shop_id)
                    except UnsupportedShopError:
                        self.shops[shop_id] = None
                shop = self.shops[shop_id]
                if shop is None:
                    self.skipped.append(question["id"])
                    continue
                used = sum(row["total_tokens"] for row in self.results) + self.failed_tokens
                limit = self.config.max_total_tokens
                if limit is not None and used >= limit:
                    self.stopped = "total_token_limit"
                    return None
                return question, shop
            return None

    def _run_one(self, question: dict[str, Any], shop: ShopContext) -> None:
        id_, config = question["id"], self.config
        started = time.monotonic()
        if config.agent == "graph":
            result = run_graph(
                question["question"],
                as_of=question["as_of_date"],
                dataset=shop.dataset,
                documents=shop.documents,
                llm=self.llm,
                run_id=id_,
                limits=self.limits,
                checkpointer=self.checkpointer,
                thread_id=thread_id(config.output_dir, id_),
                charts=config.reports,
            )
        else:
            result = run_agent(
                question["question"],
                as_of=question["as_of_date"],
                dataset=shop.dataset,
                documents=shop.documents,
                llm=self.llm,
                run_id=id_,
                limits=self.limits,
            )
        latency = time.monotonic() - started
        totals = self.ledger.totals(id_)

        # A provider failure says nothing about the question, so keep it open for a later run.
        if result.stop_reason == "llm_error":
            with self.lock:
                self.failed[id_] = (result.error or "unknown provider error")[:MAX_ERROR_CHARS]
                self.failed_tokens += totals.total_tokens
                self.consecutive_failures += 1
                if self.consecutive_failures >= config.max_consecutive_failures:
                    self.stopped = self.stopped or "provider_failures"
            return

        row = {
            "id": id_,
            "shop": question["business_id"],
            "stop_reason": result.stop_reason,
            "iterations": result.iterations,
            "llm_calls": totals.calls,
            "input_tokens": totals.input_tokens,
            "output_tokens": totals.output_tokens,
            "total_tokens": totals.total_tokens,
            "cost_usd": totals.cost_usd,
            "latency_seconds": latency,
            "failed_tools": sum(not step.ok for step in result.steps),
            "sql": [step.sql for step in result.steps if step.sql],
            "error_kinds": [step.error_kind for step in result.steps if not step.ok],
            "verify_log": result.verify_log,
            "errors": [
                (step.error or "")[:MAX_ERROR_CHARS] for step in result.steps if not step.ok
            ],
        }
        if config.reports:
            reports_dir = config.output_dir / "reports"
            reports_dir.mkdir(exist_ok=True)
            page = render_report(
                result, question=question["question"], as_of=question["as_of_date"]
            )
            (reports_dir / f"{id_}.html").write_text(page, encoding="utf-8")
        with self.lock:
            self.consecutive_failures = 0
            _append_jsonl(self.submission_path, to_submission(id_, result))
            _append_jsonl(self.results_path, row)
            self.results.append(row)
        if config.delay_seconds:
            self.sleep(config.delay_seconds)


def score_run(
    answers_path: Path,
    output_dir: Path,
    splits_path: Path,
    question_set: str,
    questions_path: Path | None = None,
) -> dict[str, Any]:
    """Score in a separate process, so this one never loads the answers."""
    submission = output_dir / "submission.jsonl"
    if not submission.exists():
        submission.touch()
    finished = subprocess.run(  # noqa: S603 - fixed module, paths from our own arguments
        [
            sys.executable,
            "-m",
            "eval.scorer.cli",
            "--answers",
            str(answers_path),
            "--submission",
            str(submission),
            "--splits",
            str(splits_path),
            "--set",
            question_set,
            *(["--questions", str(questions_path)] if questions_path else []),
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=Path(__file__).resolve().parents[2],
    )
    if finished.returncode != 0:
        raise ValueError(f"scorer failed: {finished.stderr.strip()}")
    summary = json.loads(finished.stdout)
    (output_dir / "score.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the agent over a DataMate question set.")
    parser.add_argument("--data-dir", type=Path, required=True)
    which = parser.add_mutually_exclusive_group(required=True)
    which.add_argument("--set", dest="question_set")
    which.add_argument(
        "--all-questions",
        action="store_true",
        help="every question of another split, such as public_test; no scoring",
    )
    parser.add_argument(
        "--routes", required=True, help="groq:MODEL,gemini:MODEL, in fallback order"
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--splits", type=Path, default=DEFAULT_SPLITS)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--shops", nargs="*", help="only questions about these shops")
    parser.add_argument(
        "--holdout", action="store_true", help="allow --set holdout: the one run at the end"
    )
    parser.add_argument("--max-iterations", type=int, default=10)
    parser.add_argument("--max-tokens-per-question", type=int, default=60_000)
    parser.add_argument("--max-total-tokens", type=int, required=True)
    parser.add_argument("--delay", type=float, default=0.0, help="seconds between questions")
    parser.add_argument(
        "--workers", type=int, default=1, help="questions at the same time; needs --datasets-dir"
    )
    parser.add_argument("--otlp-endpoint", help="for example http://localhost:6006/v1/traces")
    parser.add_argument("--record-content", action="store_true", help="store prompts in traces")
    parser.add_argument("--no-score", action="store_true")
    parser.add_argument(
        "--datasets-dir",
        type=Path,
        help="prepared datasets, queried in the sandbox; without it, pos.sqlite runs in process",
    )
    parser.add_argument("--image", default="da-agent-sandbox:dev")
    parser.add_argument("--agent", choices=["loop", "graph"], default="loop")
    parser.add_argument(
        "--checkpoint-url", help="Postgres URL; graph runs then survive a killed eval process"
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="graph agent: offer charts, write reports/<id>.html; not comparable with baselines",
    )
    args = parser.parse_args()

    provider = None
    if args.otlp_endpoint:
        provider = configure_tracing(args.otlp_endpoint, set_global=True)
    try:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        ledger = CostLedger(args.output_dir / "ledger.jsonl")
        routes = build_routes(args.routes, os.environ)
        llm = LLMClient(routes, ledger, record_content=args.record_content)
        if args.all_questions:
            questions = all_questions(args.data_dir, args.splits, args.limit, args.shops)
        else:
            questions = select_questions(
                args.data_dir, args.splits, args.question_set, args.limit, args.shops,
                holdout=args.holdout,
            )  # fmt: skip
        config = RunConfig(
            output_dir=args.output_dir,
            max_iterations=args.max_iterations,
            max_tokens_per_question=args.max_tokens_per_question,
            max_total_tokens=args.max_total_tokens,
            delay_seconds=args.delay,
            agent=args.agent,
            reports=args.report,
            workers=args.workers,
        )
        opener = None
        if args.datasets_dir:
            datasets = args.datasets_dir.resolve()
            runner = Runner(RunnerConfig(image=args.image, datasets_root=datasets))

            def opener(shop_id: str) -> ShopContext:
                return open_prepared_shop(args.data_dir, shop_id, datasets, runner)

        with ExitStack() as stack:
            saver = None
            if args.checkpoint_url:
                saver = stack.enter_context(postgres_checkpointer(args.checkpoint_url))
            metrics = run_questions(
                questions, args.data_dir, llm, ledger, config, open_shop_fn=opener,
                checkpointer=saver,
            )  # fmt: skip
        report: dict[str, Any] = {"metrics": metrics}
        # Another split ships no answers, so it cannot be scored here.
        if not args.no_score and not args.all_questions:
            report["score"] = score_run(
                args.data_dir / "answers.jsonl", args.output_dir, args.splits, args.question_set,
                questions_path=args.data_dir / "questions.jsonl",
            )  # fmt: skip
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    finally:
        if provider:
            provider.shutdown()
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
