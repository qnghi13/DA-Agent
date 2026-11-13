"""Prepare clean datasets for DataMate dev shops: ingest, propose a spec, and build, in sandboxes.

Build the sandbox image first, then run from the repo root:

docker build -f sandbox/Dockerfile -t da-agent-sandbox:dev .
uv run --env-file .env python -m eval.datamate.prepare --data-dir train \\
    --routes groq:openai/gpt-oss-20b --output-dir eval/reports/prepare --max-cost 0.10

Rebuild saved specs, with the calendar added and no model call:

uv run python -m eval.datamate.prepare --data-dir train --output-dir eval/reports/rebuild \\
    --reuse-specs eval/reports/datasets-dev --max-cost 0

Shop files are copied into an uploads directory, because Runner mounts must sit under its roots.
Holdout shops are refused unless --holdout is given, for the one holdout run at the end.
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from da_agent.agent.rules import split_sections
from da_agent.ingestion.spec import ColumnSpec, DatasetSpec, TableSpec
from da_agent.llm.client import CostLedger, LLMClient
from da_agent.llm.factory import build_routes
from da_agent.runner.runner import Runner
from eval.ingestion.prepare import make_runner, prepare_uploads, write_summary

DEFAULT_SPLITS = Path("eval/splits/splits.json")
CALENDAR = "calendar.csv"
# The rulebook says the calendar sits next to the shop files, so it travels with each upload.
CALENDAR_SPEC = TableSpec(
    source="calendar",
    name="calendar",
    description="One row per solar date, with its lunar date and public holiday, if any.",
    columns=[
        ColumnSpec(source="date", name="date", type="date"),
        ColumnSpec(source="weekday", name="weekday", type="integer"),
        ColumnSpec(source="lunar_day", name="lunar_day", type="integer"),
        ColumnSpec(source="lunar_month", name="lunar_month", type="integer"),
        ColumnSpec(source="lunar_year", name="lunar_year", type="integer"),
        ColumnSpec(source="lunar_leap", name="lunar_leap", type="boolean"),
        ColumnSpec(source="holiday", name="holiday"),
    ],
)


def with_calendar(spec: DatasetSpec) -> DatasetSpec:
    """Add the calendar table to a saved spec that predates it."""
    if any(table.source == CALENDAR_SPEC.source for table in spec.tables):
        return spec
    return spec.model_copy(update={"tables": [*spec.tables, CALENDAR_SPEC]})


def load_specs(directory: Path, shops: list[str]) -> dict[str, DatasetSpec]:
    """Saved specs from a datasets directory, one <shop>/spec.json each, with the calendar."""
    specs = {}
    for shop in shops:
        path = directory / shop / "spec.json"
        if path.is_file():
            specs[shop] = with_calendar(
                DatasetSpec.model_validate_json(path.read_text(encoding="utf-8"))
            )
    return specs


def select_shops(splits_path: Path, shops: list[str] | None, *, holdout: bool = False) -> list[str]:
    """Dev shops by default. With holdout=True, holdout shops only, for the one run at the end."""
    splits = json.loads(splits_path.read_text(encoding="utf-8"))
    group = "holdout_shops" if holdout else "dev_shops"
    chosen = shops or list(splits[group])
    if not holdout:
        refused = sorted(set(chosen) & set(splits["holdout_shops"]))
        if refused:
            raise ValueError(f"refusing holdout shops: {refused}")
    unknown = sorted(set(chosen) - set(splits[group]))
    if unknown:
        raise ValueError(f"unknown shops: {unknown}")
    return chosen


def shops_in(data_dir: Path, splits_path: Path) -> list[str]:
    """Every shop directory of a split, such as a test split, whose shops the splits file does not
    list. Training holdout shops are still refused."""
    shops = sorted(p.name for p in (data_dir / "data").iterdir() if p.is_dir())
    holdout = set(json.loads(splits_path.read_text(encoding="utf-8"))["holdout_shops"])
    refused = sorted(set(shops) & holdout)
    if refused:
        raise ValueError(f"refusing holdout shops: {refused}")
    return shops


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
    llm: LLMClient | None,
    ledger: CostLedger,
    *,
    max_cost: float,
    max_tokens_per_shop: int,
    specs: dict[str, DatasetSpec] | None = None,
    workers: int = 1,
) -> list[dict[str, Any]]:
    return prepare_uploads(
        {shop: data_dir / "data" / shop for shop in shops},
        output_dir, runner, llm, ledger,
        description=file_conventions(data_dir),
        max_cost=max_cost, max_tokens_per_upload=max_tokens_per_shop,
        extra_files=[data_dir / CALENDAR], specs=specs, workers=workers,
    )  # fmt: skip


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare clean datasets for DataMate dev shops.")
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--routes", help="needed unless every shop has a saved spec")
    parser.add_argument(
        "--reuse-specs", type=Path, help="datasets directory whose spec.json files are rebuilt"
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--splits", type=Path, default=DEFAULT_SPLITS)
    parser.add_argument("--shops", nargs="*")
    parser.add_argument("--holdout", action="store_true", help="prepare holdout shops instead")
    parser.add_argument(
        "--all-shops", action="store_true", help="every shop in <data-dir>/data, for a test split"
    )
    parser.add_argument("--image", default="da-agent-sandbox:dev")
    parser.add_argument("--max-cost", type=float, required=True)
    parser.add_argument("--max-tokens-per-shop", type=int, default=150_000)
    parser.add_argument("--workers", type=int, default=1, help="shops at the same time")
    args = parser.parse_args()

    if args.all_shops:
        shops = [s for s in shops_in(args.data_dir, args.splits) if s in (args.shops or [s])]
    else:
        shops = select_shops(args.splits, args.shops, holdout=args.holdout)
    root = args.output_dir.resolve()
    runner = make_runner(root, args.image)
    ledger = CostLedger(root / "ledger.jsonl")
    specs = load_specs(args.reuse_specs, shops) if args.reuse_specs else None
    llm = LLMClient(build_routes(args.routes, os.environ), ledger) if args.routes else None
    summary = prepare_shops(
        shops, args.data_dir, root, runner, llm, ledger,
        max_cost=args.max_cost, max_tokens_per_shop=args.max_tokens_per_shop, specs=specs,
        workers=args.workers,
    )  # fmt: skip
    write_summary(root, summary, ledger)
    return 0


if __name__ == "__main__":
    sys.exit(main())
