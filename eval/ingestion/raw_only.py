"""Run raw ingestion locally on upload folders, with no sandbox and no model.

This is the code-only stage that layout targets score, so it is the free baseline. Each shop gets
`<output>/<shop>/raw.duckdb` and `report.json`. `--layouts` takes a JSON file of
{shop: LayoutSpec} to apply instead, such as hand-written layouts that test the operators.
`--layout-routes` asks that model for layouts of flagged families (it costs money), tries each
candidate by a local ingest, and keeps the best, as the Worker does with sandbox ingests.

uv run --env-file .env python -m eval.ingestion.raw_only --uploads private_test/data \
    --shops pvt_004 --output-dir eval/reports/layout-model \
    --layout-routes groq:openai/gpt-oss-120b --max-cost 0.20

uv run python -m eval.ingestion.raw_only --uploads private_test/data --shops pvt_004 pvt_006 \\
    --output-dir eval/reports/layout-raw
"""

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

from da_agent.ingestion.layout_spec import LayoutSpec
from da_agent.ingestion.propose_layout import propose_layout
from da_agent.ingestion.raw import ingest
from da_agent.ingestion.report import IngestReport
from da_agent.llm.client import CostLedger, LLMClient
from da_agent.llm.factory import build_routes


def with_model(
    upload: Path, out: Path, llm: LLMClient, *, run_id: str, candidates: int, rounds: int
) -> dict:
    """Ingest, ask the model for layouts of flagged families, keep the best candidate's output in
    `out`. Returns a summary row."""
    fast = out.with_name(f"{out.name}.fast")
    for path in (out, fast):
        shutil.rmtree(path, ignore_errors=True)
    report = ingest(upload, fast)
    tried: list[tuple[LayoutSpec, Path]] = []

    def try_layout(spec: LayoutSpec) -> IngestReport | None:
        target = out.with_name(f"{out.name}.try{len(tried) + 1}")
        shutil.rmtree(target, ignore_errors=True)
        tried.append((spec, target))
        try:
            return ingest(upload, target, layout=spec)
        except Exception:  # a failed candidate scores nothing, as a failed sandbox ingest
            return None

    result = propose_layout(
        report, report.documents, llm, run_id=run_id, try_layout=try_layout,
        candidates=candidates, max_rounds=rounds,
    )  # fmt: skip
    chosen = next((t for s, t in reversed(tried) if s == result.spec), None)
    keep = chosen if chosen is not None and result.spec is not None else fast
    keep.rename(out)
    for _, target in tried:
        shutil.rmtree(target, ignore_errors=True)
    shutil.rmtree(fast, ignore_errors=True)
    final = result.report if keep is not fast and result.report else report
    (out / "report.json").write_text(final.model_dump_json(indent=2), encoding="utf-8")
    if result.spec is not None:
        (out / "layout.json").write_text(result.spec.model_dump_json(indent=1), encoding="utf-8")
    proposals = [
        {"score": t.score, "problems": t.problems, "spec": t.spec.model_dump(mode="json")}
        for t in result.tried
    ]
    text = json.dumps(proposals, ensure_ascii=False, indent=1)
    (out / "candidates.json").write_text(text, encoding="utf-8")
    return {
        "shop": run_id, "families": len(report.layout), "rounds": result.rounds,
        "scores": result.scores, "error": result.error, "problems": result.problems[:5],
        "kept": "layout" if keep is not fast else "fast path",
    }  # fmt: skip


def run(
    uploads: Path,
    shops: list[str],
    output_dir: Path,
    layouts: dict[str, LayoutSpec] | None = None,
) -> dict[str, int]:
    """Ingest each shop and return its table count."""
    counts: dict[str, int] = {}
    for shop in shops:
        out = output_dir / shop
        if out.exists():
            shutil.rmtree(out)
        out.mkdir(parents=True)
        report = ingest(uploads / shop, out, layout=(layouts or {}).get(shop))
        (out / "report.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
        counts[shop] = len(report.tables)
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description="Raw ingest upload folders locally.")
    parser.add_argument("--uploads", type=Path, required=True, help="one folder per shop")
    parser.add_argument("--shops", nargs="*", help="shop folder names; all when omitted")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--layouts", type=Path, help="JSON {shop: LayoutSpec} to apply")
    parser.add_argument("--layout-routes", help="model routes that propose layouts (costs money)")
    parser.add_argument("--max-cost", type=float, default=0.0, help="stop asking past this, USD")
    parser.add_argument("--candidates", type=int, default=2)
    parser.add_argument("--rounds", type=int, default=3)
    args = parser.parse_args()
    layouts = None
    if args.layouts:
        raw = json.loads(args.layouts.read_text(encoding="utf-8"))
        layouts = {shop: LayoutSpec.model_validate(spec) for shop, spec in raw.items()}

    shops = args.shops or sorted(p.name for p in args.uploads.iterdir() if p.is_dir())
    if args.layout_routes:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        ledger = CostLedger(args.output_dir / "ledger.jsonl")
        llm = LLMClient(build_routes(args.layout_routes, os.environ), ledger)
        rows = []
        for shop in shops:
            if ledger.totals().cost_usd >= args.max_cost:
                rows.append({"shop": shop, "kept": "skipped: cost limit"})
                continue
            row = with_model(
                args.uploads / shop, args.output_dir / shop, llm, run_id=shop,
                candidates=args.candidates, rounds=args.rounds,
            )  # fmt: skip
            totals = ledger.totals(shop)
            row |= {"calls": totals.calls, "tokens": totals.total_tokens,
                    "cost_usd": round(totals.cost_usd, 5)}  # fmt: skip
            print(json.dumps(row, ensure_ascii=False), flush=True)
            rows.append(row)
        (args.output_dir / "summary.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
        totals = ledger.totals()
        print(f"total: {totals.calls} calls, {totals.total_tokens} tokens, ${totals.cost_usd:.4f}")
        return 0
    for shop, tables in run(args.uploads, shops, args.output_dir, layouts).items():
        print(f"{shop}: {tables} tables")
    return 0


if __name__ == "__main__":
    sys.exit(main())
