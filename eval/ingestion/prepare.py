"""Prepare clean datasets for a directory of uploads: ingest, propose a spec, build, in sandboxes.

Each subdirectory of --uploads is one upload. Build the sandbox image first, then run from the repo
root:

uv run --env-file .env python -m eval.ingestion.prepare --uploads eval/reports/synthetic/uploads \\
    --routes groq:openai/gpt-oss-20b --output-dir eval/reports/synthetic --max-cost 0.05

Files are copied into the output's uploads directory, because Runner mounts must sit under its
roots.
"""

import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from da_agent.ingestion.pipeline import parse_problems, prepare_dataset, rebuild_dataset
from da_agent.ingestion.spec import DatasetSpec
from da_agent.llm.client import CostLedger, LLMClient, RunBudget
from da_agent.llm.factory import build_routes
from da_agent.runner.runner import Runner, RunnerConfig


def copy_upload(source: Path, target: Path, extra_files: list[Path] | None = None) -> None:
    """Copy an upload and any extra files into it, readable by the sandbox user."""
    shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(source, target, symlinks=False)
    for extra in extra_files or []:
        shutil.copyfile(extra, target / extra.name)
    for path in [target, *target.rglob("*")]:
        path.chmod(0o755 if path.is_dir() else 0o644)


def make_runner(root: Path, image: str) -> Runner:
    for name in ("uploads", "staging", "datasets"):
        (root / name).mkdir(parents=True, exist_ok=True)
    return Runner(
        RunnerConfig(
            image=image,
            datasets_root=root / "datasets",
            uploads_root=root / "uploads",
            staging_root=root / "staging",
        )
    )


def prepare_uploads(
    sources: dict[str, Path],
    output_dir: Path,
    runner: Runner,
    llm: LLMClient | None,
    ledger: CostLedger,
    *,
    description: str,
    max_cost: float,
    max_tokens_per_upload: int,
    extra_files: list[Path] | None = None,
    specs: dict[str, DatasetSpec] | None = None,
) -> list[dict[str, Any]]:
    """Prepare each named upload into output_dir/datasets/<name>, and summarise each run.

    Uploads with a saved spec in `specs` are rebuilt with it and no model call."""
    uploads = runner.config.uploads_root
    if uploads is None:
        raise ValueError("the runner needs an uploads root")
    summary: list[dict[str, Any]] = []
    for name, source in sources.items():
        if source.resolve() != (uploads / name).resolve():
            copy_upload(source, uploads / name, extra_files)
        if specs is not None and name in specs:
            prepared = rebuild_dataset(name, runner, specs[name])
        elif llm is None:
            summary.append({"shop": name, "status": "skipped_no_spec"})
            continue
        elif ledger.totals().cost_usd >= max_cost:
            summary.append({"shop": name, "status": "skipped_cost_limit"})
            continue
        else:
            prepared = prepare_dataset(
                name,
                runner,
                llm,
                run_id=name,
                description=description,
                budget=RunBudget(max_total_tokens=max_tokens_per_upload),
            )
        totals = ledger.totals(name)
        row: dict[str, Any] = {
            "shop": name,
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
            target = output_dir / "datasets" / name
            shutil.rmtree(target, ignore_errors=True)
            shutil.move(str(build.output_dir), target)
            if prepared.spec is not None:
                (target / "spec.json").write_text(
                    prepared.spec.model_dump_json(indent=1), encoding="utf-8"
                )
            columns = [c for t in build.report.tables for c in t.columns]
            typed = [c for c in columns if c.type != "text"]
            row["tables"] = len(build.report.tables)
            row["typed_columns"] = len(typed)
            row["min_parse_rate"] = round(min((c.parse_rate for c in typed), default=1.0), 4)
            row["remaining_problems"] = parse_problems(build.report)
            row["hidden_columns"] = sum(len(t.hidden) for t in build.report.tables)
            row["excluded_rows"] = sum(e.rows for t in build.report.tables for e in t.excluded)
            row["dropped_rules"] = prepared.dropped_rules
            row["deduplicated"] = prepared.deduplicated
            row["unproven_repeats"] = {
                t.name: t.duplicate_rows for t in build.report.tables if t.duplicate_rows
            }
        summary.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    return summary


def write_summary(root: Path, summary: list[dict[str, Any]], ledger: CostLedger) -> None:
    (root / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    totals = ledger.totals()
    print(f"total: {totals.calls} calls, {totals.total_tokens} tokens, ${totals.cost_usd:.4f}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepare clean datasets for a directory of uploads."
    )
    parser.add_argument("--uploads", type=Path, required=True)
    parser.add_argument("--routes", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--description", default="")
    parser.add_argument("--image", default="da-agent-sandbox:dev")
    parser.add_argument("--max-cost", type=float, required=True)
    parser.add_argument("--max-tokens-per-upload", type=int, default=150_000)
    args = parser.parse_args()

    root = args.output_dir.resolve()
    sources = {p.name: p for p in sorted(args.uploads.iterdir()) if p.is_dir()}
    runner = make_runner(root, args.image)
    ledger = CostLedger(root / "ledger.jsonl")
    llm = LLMClient(build_routes(args.routes, os.environ), ledger)
    summary = prepare_uploads(
        sources, root, runner, llm, ledger,
        description=args.description, max_cost=args.max_cost,
        max_tokens_per_upload=args.max_tokens_per_upload,
    )  # fmt: skip
    write_summary(root, summary, ledger)
    return 0


if __name__ == "__main__":
    sys.exit(main())
