"""Prepare clean datasets for DataMate dev shops: ingest, propose a spec, and build, in sandboxes.

Build the sandbox image first, then run from the repo root:

docker build -f sandbox/Dockerfile -t da-agent-sandbox:dev .
uv run --env-file .env python -m eval.datamate.prepare --data-dir train \\
    --routes groq:openai/gpt-oss-20b --output-dir eval/reports/prepare --max-cost 0.10

Shop files are copied into an uploads directory, because Runner mounts must sit under its roots.
Holdout shops are refused.
"""

import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from da_agent.agent.rules import split_sections
from da_agent.ingestion.pipeline import parse_problems, prepare_dataset
from da_agent.llm.client import CostLedger, LLMClient, RunBudget
from da_agent.llm.factory import build_routes
from da_agent.runner.runner import Runner, RunnerConfig

DEFAULT_SPLITS = Path("eval/splits/splits.json")


def select_shops(splits_path: Path, shops: list[str] | None) -> list[str]:
    splits = json.loads(splits_path.read_text(encoding="utf-8"))
    holdout = set(splits["holdout_shops"])
    chosen = shops or list(splits["dev_shops"])
    refused = sorted(set(chosen) & holdout)
    if refused:
        raise ValueError(f"refusing holdout shops: {refused}")
    unknown = sorted(set(chosen) - set(splits["dev_shops"]))
    if unknown:
        raise ValueError(f"unknown shops: {unknown}")
    return chosen


def file_conventions(data_dir: Path) -> str:
    """Rulebook section 8, which documents how the shop files are written. It acts as the user's
    description of the data. Other sections are about questions and answers, not files."""
    _, sections = split_sections((data_dir / "rulebook.md").read_text(encoding="utf-8"))
    return "\n\n".join(s.text for s in sections if s.title.startswith("8."))


def copy_upload(source: Path, target: Path) -> None:
    """Copy a shop as an upload, readable by the sandbox user."""
    shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(source, target, symlinks=False)
    for path in [target, *target.rglob("*")]:
        path.chmod(0o755 if path.is_dir() else 0o644)


def prepare_shops(
    shops: list[str],
    data_dir: Path,
    output_dir: Path,
    runner: Runner,
    llm: LLMClient,
    ledger: CostLedger,
    *,
    max_cost: float,
    max_tokens_per_shop: int,
) -> list[dict[str, Any]]:
    uploads = runner.config.uploads_root
    if uploads is None:
        raise ValueError("the runner needs an uploads root")
    description = file_conventions(data_dir)
    summary: list[dict[str, Any]] = []
    for shop in shops:
        if ledger.totals().cost_usd >= max_cost:
            summary.append({"shop": shop, "status": "skipped_cost_limit"})
            continue
        copy_upload(data_dir / "data" / shop, uploads / shop)
        prepared = prepare_dataset(
            shop,
            runner,
            llm,
            run_id=shop,
            description=description,
            budget=RunBudget(max_total_tokens=max_tokens_per_shop),
        )
        totals = ledger.totals(shop)
        row: dict[str, Any] = {
            "shop": shop,
            "status": prepared.status,
            "llm_calls": totals.calls,
            "tokens": totals.total_tokens,
            "cost_usd": round(totals.cost_usd, 5),
            "ingest_seconds": round(prepared.ingest.duration_seconds, 1),
        }
        if prepared.proposal is not None:
            row["attempts"] = prepared.proposal.attempts
            row["problems"] = prepared.proposal.problems
            row["error"] = prepared.proposal.error
        build = prepared.build
        if build is not None and build.report is not None and build.output_dir is not None:
            target = output_dir / "datasets" / shop
            shutil.rmtree(target, ignore_errors=True)
            shutil.move(str(build.output_dir), target)
            if prepared.proposal is not None:
                (target / "spec.json").write_text(
                    prepared.proposal.spec.model_dump_json(indent=1), encoding="utf-8"
                )
            columns = [c for t in build.report.tables for c in t.columns]
            typed = [c for c in columns if c.type != "text"]
            row["tables"] = len(build.report.tables)
            row["typed_columns"] = len(typed)
            row["min_parse_rate"] = round(min((c.parse_rate for c in typed), default=1.0), 4)
            row["remaining_problems"] = parse_problems(build.report)
            row["hidden_columns"] = sum(len(t.hidden) for t in build.report.tables)
        summary.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare clean datasets for DataMate dev shops.")
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--routes", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--splits", type=Path, default=DEFAULT_SPLITS)
    parser.add_argument("--shops", nargs="*")
    parser.add_argument("--image", default="da-agent-sandbox:dev")
    parser.add_argument("--max-cost", type=float, required=True)
    parser.add_argument("--max-tokens-per-shop", type=int, default=150_000)
    args = parser.parse_args()

    shops = select_shops(args.splits, args.shops)
    root = args.output_dir.resolve()
    for name in ("uploads", "staging", "datasets"):
        (root / name).mkdir(parents=True, exist_ok=True)
    ledger = CostLedger(root / "ledger.jsonl")
    llm = LLMClient(build_routes(args.routes, os.environ), ledger)
    runner = Runner(
        RunnerConfig(
            image=args.image,
            datasets_root=root / "datasets",
            uploads_root=root / "uploads",
            staging_root=root / "staging",
        )
    )
    summary = prepare_shops(
        shops, args.data_dir, root, runner, llm, ledger,
        max_cost=args.max_cost, max_tokens_per_shop=args.max_tokens_per_shop,
    )  # fmt: skip
    (root / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    totals = ledger.totals()
    print(f"total: {totals.calls} calls, {totals.total_tokens} tokens, ${totals.cost_usd:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
