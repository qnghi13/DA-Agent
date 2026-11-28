"""Score raw tables against hand-written layout targets.

A target is one JSON line: {"dataset", "source": "<file#sheet>", "rows": <int>, "samples":
[[cell text, ...], ...]}. It passes when the raw tables hold exactly `rows` rows from that source,
in any number of tables, and each sample's texts all sit in one of those rows. Matching is by
value after trimming and Unicode NFC, so the column names a layout chose do not matter. Meta
columns (`_source`, `_row`, `_cell`) are not values.

uv run python -m eval.ingestion.layout_targets --targets private_test/layout_targets/dev.jsonl \\
    --ingests eval/reports/layout-raw
"""

import argparse
import json
import sys
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import duckdb

RAW_DATABASE = "raw.duckdb"
_META = {"_source", "_row", "_cell"}


@dataclass(frozen=True)
class LayoutTarget:
    dataset: str
    source: str
    rows: int
    samples: list[list[str]]


@dataclass(frozen=True)
class TargetResult:
    target: LayoutTarget
    found_rows: int
    missing_samples: list[list[str]]
    passed: bool


def load_targets(path: Path) -> list[LayoutTarget]:
    targets = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            item = json.loads(line)
            targets.append(
                LayoutTarget(
                    dataset=item["dataset"],
                    source=item["source"],
                    rows=int(item["rows"]),
                    samples=[[str(value) for value in sample] for sample in item["samples"]],
                )
            )
    return targets


def _norm(value: object) -> str:
    return unicodedata.normalize("NFC", str(value).strip())


def _rows_by_source(raw_db: Path, sources: set[str]) -> dict[str, list[set[str]]]:
    """Each row from the wanted sources as the set of its normalized non-blank values."""
    rows: dict[str, list[set[str]]] = defaultdict(list)
    with duckdb.connect(str(raw_db), read_only=True) as connection:
        names = [row[0] for row in connection.execute("SHOW TABLES").fetchall()]
        for name in names:
            columns = [row[0] for row in connection.execute(f'DESCRIBE "{name}"').fetchall()]
            if "_source" not in columns:
                continue
            values = [c for c in columns if c not in _META]
            selected = ", ".join(f'"{c}"' for c in ["_source", *values])
            query = f'SELECT {selected} FROM "{name}" WHERE _source IN ?'  # noqa: S608
            for source, *cells in connection.execute(query, [list(sources)]).fetchall():
                rows[source].append({_norm(c) for c in cells if c is not None and _norm(c)})
    return rows


def score_dataset(raw_db: Path, targets: list[LayoutTarget]) -> list[TargetResult]:
    rows = _rows_by_source(raw_db, {t.source for t in targets}) if targets else {}
    results = []
    for target in targets:
        found = rows.get(target.source, [])
        missing = [
            sample
            for sample in target.samples
            if not any({_norm(v) for v in sample} <= row for row in found)
        ]
        results.append(
            TargetResult(
                target=target,
                found_rows=len(found),
                missing_samples=missing,
                passed=len(found) == target.rows and not missing,
            )
        )
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Score raw tables against layout targets.")
    parser.add_argument("--targets", type=Path, required=True)
    parser.add_argument(
        "--ingests", type=Path, required=True, help=f"one <dataset>/{RAW_DATABASE} per dataset"
    )
    parser.add_argument("--verbose", action="store_true", help="print each failed target")
    args = parser.parse_args()

    by_dataset: dict[str, list[LayoutTarget]] = defaultdict(list)
    for target in load_targets(args.targets):
        by_dataset[target.dataset].append(target)

    total_passed = total = 0
    print(f"{'dataset':<16} {'passed':>6} {'targets':>7}")
    for dataset, targets in sorted(by_dataset.items()):
        database = args.ingests / dataset / RAW_DATABASE
        if database.exists():
            results = score_dataset(database, targets)
        else:
            results = [TargetResult(t, 0, t.samples, False) for t in targets]
        passed = sum(r.passed for r in results)
        total_passed, total = total_passed + passed, total + len(results)
        print(f"{dataset:<16} {passed:>6} {len(results):>7}")
        if args.verbose:
            for r in results:
                if not r.passed:
                    print(
                        f"  FAIL {r.target.source}: rows {r.found_rows}/{r.target.rows}, "
                        f"{len(r.missing_samples)}/{len(r.target.samples)} samples missing"
                    )
    print(f"{'total':<16} {total_passed:>6} {total:>7}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
