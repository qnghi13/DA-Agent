"""Run the agent over a question set. Example, from the repo root:

uv run --env-file .env python -m eval.datamate.run --data-dir train --set bootstrap --limit 3 \
    --routes groq:MODEL,gemini:MODEL --output-dir eval/reports/first --max-total-tokens 200000 \
    --datasets-dir eval/reports/datasets-dev

With --datasets-dir, every shop is a prepared clean dataset and SQL runs in the sandbox (ADR 0011).

The run never reads answers.jsonl. Scoring happens afterwards in a separate scorer process.
"""

import argparse
import json
import os
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from da_agent.agent.loop import AgentLimits, run_agent
from da_agent.llm.client import CostLedger, LLMClient, RunBudget
from da_agent.llm.factory import build_routes
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


def select_questions(
    data_dir: Path,
    splits_path: Path,
    question_set: str,
    limit: int | None = None,
    shops: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Return the questions in a set, optionally only for some shops. Refuses any set that
    touches a holdout shop."""
    splits = json.loads(splits_path.read_text(encoding="utf-8"))
    if question_set not in splits["question_sets"]:
        raise ValueError(f"unknown question set {question_set!r}")
    wanted = set(splits["question_sets"][question_set])
    holdout = set(splits["holdout_shops"])
    questions = [row for row in load_jsonl(data_dir / "questions.jsonl") if row["id"] in wanted]
    touched = sorted({row["business_id"] for row in questions} & holdout)
    if touched:
        raise ValueError(f"question set {question_set!r} touches holdout shops: {touched}")
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
    }


def run_questions(
    questions: list[dict[str, Any]],
    data_dir: Path,
    llm: LLMClient,
    ledger: CostLedger,
    config: RunConfig,
    sleep: Callable[[float], None] = time.sleep,
    open_shop_fn: Callable[[str], ShopContext] | None = None,
) -> dict[str, Any]:
    """Run questions in order, saving after each one so that a later run can resume.

    open_shop_fn opens a shop by id. By default it opens the shop's pos.sqlite in this process."""
    opener = open_shop_fn or (lambda shop_id: open_shop(data_dir, shop_id))
    config.output_dir.mkdir(parents=True, exist_ok=True)
    submission_path = config.output_dir / "submission.jsonl"
    results_path = config.output_dir / "results.jsonl"
    results = load_jsonl(results_path) if results_path.exists() else []
    done = {row["id"] for row in results}
    budget = RunBudget(max_total_tokens=config.max_tokens_per_question)
    limits = AgentLimits(max_iterations=config.max_iterations, budget=budget)

    shops: dict[str, ShopContext | None] = {}
    skipped: list[str] = []
    failed: dict[str, str] = {}
    failed_tokens = 0
    stopped: str | None = None
    consecutive_failures = 0

    for question in questions:
        id_, shop_id = question["id"], question["business_id"]
        if id_ in done:
            continue
        if shop_id not in shops:
            try:
                shops[shop_id] = opener(shop_id)
            except UnsupportedShopError:
                shops[shop_id] = None
        shop = shops[shop_id]
        if shop is None:
            skipped.append(id_)
            continue
        used = sum(row["total_tokens"] for row in results) + failed_tokens
        if config.max_total_tokens is not None and used >= config.max_total_tokens:
            stopped = "total_token_limit"
            break

        started = time.monotonic()
        result = run_agent(
            question["question"],
            as_of=question["as_of_date"],
            dataset=shop.dataset,
            documents=shop.documents,
            llm=llm,
            run_id=id_,
            limits=limits,
        )
        latency = time.monotonic() - started

        # A provider failure says nothing about the question, so keep it open for a later run.
        if result.stop_reason == "llm_error":
            failed[id_] = (result.error or "unknown provider error")[:MAX_ERROR_CHARS]
            failed_tokens += ledger.totals(id_).total_tokens
            consecutive_failures += 1
            if consecutive_failures >= config.max_consecutive_failures:
                stopped = "provider_failures"
                break
            continue
        consecutive_failures = 0

        totals = ledger.totals(id_)
        row = {
            "id": id_,
            "shop": shop_id,
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
            "errors": [
                (step.error or "")[:MAX_ERROR_CHARS] for step in result.steps if not step.ok
            ],
        }
        _append_jsonl(submission_path, to_submission(id_, result))
        _append_jsonl(results_path, row)
        results.append(row)
        if config.delay_seconds:
            sleep(config.delay_seconds)

    metrics = _build_metrics(results, skipped, failed, failed_tokens, stopped)
    (config.output_dir / "metrics.json").write_text(
        json.dumps(metrics, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return metrics


def score_run(
    answers_path: Path, output_dir: Path, splits_path: Path, question_set: str
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
    parser.add_argument("--set", dest="question_set", required=True)
    parser.add_argument(
        "--routes", required=True, help="groq:MODEL,gemini:MODEL, in fallback order"
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--splits", type=Path, default=DEFAULT_SPLITS)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--shops", nargs="*", help="only questions about these shops")
    parser.add_argument("--max-iterations", type=int, default=10)
    parser.add_argument("--max-tokens-per-question", type=int, default=60_000)
    parser.add_argument("--max-total-tokens", type=int, required=True)
    parser.add_argument("--delay", type=float, default=0.0, help="seconds between questions")
    parser.add_argument("--otlp-endpoint", help="for example http://localhost:6006/v1/traces")
    parser.add_argument("--record-content", action="store_true", help="store prompts in traces")
    parser.add_argument("--no-score", action="store_true")
    parser.add_argument(
        "--datasets-dir",
        type=Path,
        help="prepared datasets, queried in the sandbox; without it, pos.sqlite runs in process",
    )
    parser.add_argument("--image", default="da-agent-sandbox:dev")
    args = parser.parse_args()

    provider = None
    if args.otlp_endpoint:
        provider = configure_tracing(args.otlp_endpoint, set_global=True)
    try:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        ledger = CostLedger(args.output_dir / "ledger.jsonl")
        routes = build_routes(args.routes, os.environ)
        llm = LLMClient(routes, ledger, record_content=args.record_content)
        questions = select_questions(
            args.data_dir, args.splits, args.question_set, args.limit, args.shops
        )
        config = RunConfig(
            output_dir=args.output_dir,
            max_iterations=args.max_iterations,
            max_tokens_per_question=args.max_tokens_per_question,
            max_total_tokens=args.max_total_tokens,
            delay_seconds=args.delay,
        )
        opener = None
        if args.datasets_dir:
            datasets = args.datasets_dir.resolve()
            runner = Runner(RunnerConfig(image=args.image, datasets_root=datasets))

            def opener(shop_id: str) -> ShopContext:
                return open_prepared_shop(args.data_dir, shop_id, datasets, runner)

        report: dict[str, Any] = {
            "metrics": run_questions(
                questions, args.data_dir, llm, ledger, config, open_shop_fn=opener
            )
        }
        if not args.no_score:
            report["score"] = score_run(
                args.data_dir / "answers.jsonl", args.output_dir, args.splits, args.question_set
            )
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
