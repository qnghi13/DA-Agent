"""Run the agent over the fixed-analysis eval (ADR 0051). Real model calls cost money: ask first.

    uv run --env-file .env python -m eval.analysis.run --routes groq:openai/gpt-oss-20b \\
        --output-dir eval/reports/analysis-first --max-total-tokens 600000 [--attempts 3]

Generates the synthetic shop, builds it in the sandbox with spec.json (no model call), then asks
every question with analysis offered, as product runs do. Writes results.jsonl and metrics.json
to the output folder and prints the outcome and the cost. --build-only stops after the build.

Needs Docker and a sandbox image with the analysis code:
docker build -f sandbox/Dockerfile -t da-agent-sandbox:dev .
"""

import argparse
import json
import os
import time
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any

from da_agent.agent.answer import AgentResult, result_to_json
from da_agent.agent.graph import run_graph
from da_agent.agent.loop_types import AgentLimits, Document
from da_agent.ingestion.spec import DatasetSpec
from da_agent.llm.client import CostLedger, LLMClient, RunBudget
from da_agent.llm.replay import replay_scope
from da_agent.manifest import build_manifest
from da_agent.storage.sandbox_dataset import SandboxDataset
from eval.analysis.generate import DOCUMENT, QUESTIONS, SHOP, generate
from eval.analysis.score import Score, score
from eval.datamate.run import eval_client, eval_routes
from eval.ingestion.prepare import make_runner, prepare_uploads

SPEC = Path(__file__).with_name("spec.json")
UPLOAD = "analysis-shop"
# Exit criteria from the design (docs/design/2026-11-26-analysis-tools.md), per attempt.
CRITERIA = {"kind_right": 8, "correct": 7, "controls_without_analysis": 5}


def build(output_dir: Path, image: str, ledger: CostLedger) -> tuple[SandboxDataset, Path]:
    """Generate the shop and build it in the sandbox. Returns the dataset and the questions."""
    source = output_dir / "source"
    generate(source)
    runner = make_runner(output_dir, image)
    spec = DatasetSpec.model_validate_json(SPEC.read_text(encoding="utf-8"))
    [row] = prepare_uploads(
        {UPLOAD: source / SHOP}, output_dir, runner, None, ledger, description="",
        max_cost=0.0, max_tokens_per_upload=0, specs={UPLOAD: spec},
    )  # fmt: skip
    if row.get("status") != "ok":
        raise RuntimeError(f"the synthetic shop did not build: {row}")
    return SandboxDataset.open(runner, output_dir / "datasets", UPLOAD), source / QUESTIONS


def ask(
    question: dict[str, Any],
    attempt: int,
    dataset: SandboxDataset,
    llm: LLMClient,
    limits: AgentLimits,
    ledger: CostLedger,
) -> tuple[dict[str, Any], AgentResult]:
    run_id = f"{question['id']}-{attempt}"
    started = time.monotonic()
    with replay_scope(run_id):
        result = run_graph(
            question["question"],
            as_of=question["as_of"],
            dataset=dataset,
            documents=[Document("rules", DOCUMENT)],
            llm=llm,
            run_id=run_id,
            limits=limits,
            charts=True,
            analysis=True,
        )
    graded: Score = score(question, result)
    totals = ledger.totals(run_id)
    row = {
        "id": question["id"],
        "attempt": attempt,
        "analysis": question["analysis"],
        "kinds": graded.kinds,
        "kind_ok": graded.kind_ok,
        "outcome": graded.outcome,
        "failed": graded.failed,
        "stop_reason": result.stop_reason,
        "verify_log": result.verify_log,
        "tool_errors": [s.error_kind for s in result.steps if s.error_kind],
        "seconds": round(time.monotonic() - started, 1),
        "tokens": totals.total_tokens,
        "cost_usd": round(totals.cost_usd, 6),
    }
    return row, result


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Counts per attempt, how many questions every attempt got right (pass^k), and the totals."""
    attempts = sorted({row["attempt"] for row in rows})
    per_attempt = []
    for attempt in attempts:
        mine = [row for row in rows if row["attempt"] == attempt]
        analyses = [row for row in mine if row["analysis"] != "none"]
        controls = [row for row in mine if row["analysis"] == "none"]
        outcomes = Counter(row["outcome"] for row in mine)
        per_attempt.append(
            {
                "attempt": attempt,
                "questions": len(mine),
                "kind_right": sum(row["kind_ok"] for row in analyses),
                "analysis_questions": len(analyses),
                "correct": sum(row["outcome"] == "correct" for row in analyses),
                "controls_correct": sum(row["outcome"] == "correct" for row in controls),
                "controls_without_analysis": sum(row["kind_ok"] for row in controls),
                "controls": len(controls),
                "outcomes": dict(outcomes),
            }
        )
    by_question: dict[str, list[bool]] = {}
    for row in rows:
        by_question.setdefault(row["id"], []).append(row["outcome"] == "correct")
    return {
        "attempts": per_attempt,
        "pass_all_attempts": sum(all(results) for results in by_question.values()),
        "questions": len(by_question),
        "tool_errors": dict(Counter(kind for row in rows for kind in row["tool_errors"])),
        "verify_problems": dict(
            Counter(kind for row in rows for kind in row["verify_log"] if kind != "passed")
        ),
        "mean_seconds": round(sum(row["seconds"] for row in rows) / len(rows), 1) if rows else 0,
        "tokens": sum(row["tokens"] for row in rows),
        "cost_usd": round(sum(row["cost_usd"] for row in rows), 6),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the fixed-analysis eval.")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--routes", help="groq:MODEL,gemini:MODEL, in fallback order")
    parser.add_argument("--max-total-tokens", type=int, default=600_000)
    parser.add_argument("--max-tokens-per-question", type=int, default=60_000)
    parser.add_argument("--max-iterations", type=int, default=10)
    parser.add_argument("--max-seconds", type=float, default=300.0)
    parser.add_argument("--attempts", type=int, default=1)
    parser.add_argument("--ids", nargs="*", help="only these question ids")
    parser.add_argument("--image", default="da-agent-sandbox:dev")
    parser.add_argument("--build-only", action="store_true")
    parser.add_argument("--record", action="store_true", help="write cassette.jsonl")
    parser.add_argument("--replay", type=Path, metavar="RUN_DIR", help="answer from a cassette")
    args = parser.parse_args()
    if not args.build_only and not args.routes and not args.replay:
        parser.error("--routes is required unless --build-only or --replay")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    ledger = CostLedger(args.output_dir / "ledger.jsonl")
    dataset, questions_path = build(args.output_dir, args.image, ledger)
    print(f"built {UPLOAD}: {[t.name for t in dataset.describe()]}", flush=True)
    if args.build_only:
        return 0
    questions = [json.loads(line) for line in questions_path.read_text("utf-8").splitlines()]
    if args.ids:
        questions = [q for q in questions if q["id"] in args.ids]
    routes, _ = eval_routes(
        args.routes or "", os.environ, args.output_dir, record=args.record,
        replay_dir=args.replay, replay_miss="stop",
    )  # fmt: skip
    llm = eval_client(routes, ledger, os.environ, record_content=False,
                      replaying=args.replay is not None)  # fmt: skip
    limits = AgentLimits(
        max_iterations=args.max_iterations,
        budget=RunBudget(max_total_tokens=args.max_tokens_per_question),
        max_seconds=args.max_seconds,
    )
    manifest = build_manifest(
        routes=routes,
        limits={
            "agent": asdict(limits),
            "max_total_tokens": args.max_total_tokens,
            "agent_kind": "graph",
            "analysis": True,
            "charts": True,
        },
        env=os.environ,
    ).to_json()
    print(f"agent {manifest['version']}", flush=True)
    rows: list[dict[str, Any]] = []
    with (args.output_dir / "results.jsonl").open("w", encoding="utf-8") as results:
        for attempt in range(1, args.attempts + 1):
            for question in questions:
                if ledger.totals().total_tokens >= args.max_total_tokens:
                    print("stopped: the token limit for the run is spent", flush=True)
                    break
                row, result = ask(question, attempt, dataset, llm, limits, ledger)
                rows.append(row)
                results.write(json.dumps({**row, "result": result_to_json(result)},
                                         ensure_ascii=False, default=str) + "\n")  # fmt: skip
                results.flush()
                print(f"{row['id']} #{attempt}: {row['outcome']}, kinds {row['kinds']}, "
                      f"{row['seconds']} s, ${row['cost_usd']}", flush=True)  # fmt: skip
    metrics = {"manifest": manifest, **summarize(rows)}
    (args.output_dir / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    for attempt in metrics["attempts"]:
        print(json.dumps(attempt, ensure_ascii=False))
    print(f"pass in every attempt: {metrics['pass_all_attempts']}/{metrics['questions']}; "
          f"cost ${metrics['cost_usd']}; tokens {metrics['tokens']}")  # fmt: skip
    print(f"criteria per attempt: {CRITERIA}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
