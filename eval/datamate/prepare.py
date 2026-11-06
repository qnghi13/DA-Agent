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
import sys
from pathlib import Path
from typing import Any

from da_agent.agent.rules import split_sections
from da_agent.llm.client import CostLedger, LLMClient
from da_agent.llm.factory import build_routes
from da_agent.runner.runner import Runner
from eval.ingestion.prepare import make_runner, prepare_uploads, write_summary

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
    return prepare_uploads(
        {shop: data_dir / "data" / shop for shop in shops},
        output_dir, runner, llm, ledger,
        description=file_conventions(data_dir),
        max_cost=max_cost, max_tokens_per_upload=max_tokens_per_shop,
    )  # fmt: skip


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
    runner = make_runner(root, args.image)
    ledger = CostLedger(root / "ledger.jsonl")
    llm = LLMClient(build_routes(args.routes, os.environ), ledger)
    summary = prepare_shops(
        shops, args.data_dir, root, runner, llm, ledger,
        max_cost=args.max_cost, max_tokens_per_shop=args.max_tokens_per_shop,
    )  # fmt: skip
    write_summary(root, summary, ledger)
    return 0


if __name__ == "__main__":
    sys.exit(main())
