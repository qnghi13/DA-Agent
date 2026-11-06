"""Score the rows a DatasetSpec excluded against labels of rows that are not records.

Labels are JSON lines: {"dataset": ..., "source": "<file#sheet>", "row": <raw row>, "kind": ...}.
A row is identified by its `_source` and `_row`, which every clean and excluded row keeps.
Datasets in the labels with no labelled rows still count: any exclusion there is wrong.

uv run python -m eval.ingestion.exclusions --labels eval/ingestion/labels/dev.jsonl \\
    --datasets eval/reports/prepare-v2/datasets
"""

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import duckdb

RowId = tuple[str, int]
DATABASE = "dataset.duckdb"


@dataclass(frozen=True)
class ExclusionScore:
    hits: int
    missed: int
    wrong: int

    @property
    def precision(self) -> float | None:
        predicted = self.hits + self.wrong
        return self.hits / predicted if predicted else None

    @property
    def recall(self) -> float | None:
        labelled = self.hits + self.missed
        return self.hits / labelled if labelled else None


def load_labels(path: Path, datasets: list[str] | None = None) -> dict[str, set[RowId]]:
    """Labelled rows per dataset. Datasets named in `datasets` but without labels map to an
    empty set, so wrong exclusions in clean datasets still count."""
    labels: dict[str, set[RowId]] = defaultdict(set)
    for name in datasets or []:
        labels[name] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            label = json.loads(line)
            labels[label["dataset"]].add((label["source"], int(label["row"])))
    return dict(labels)


def excluded_rows(database: Path) -> set[RowId]:
    with duckdb.connect(str(database), read_only=True) as connection:
        tables = {row[0] for row in connection.execute("SHOW TABLES").fetchall()}
        if "_excluded" not in tables:
            return set()
        rows = connection.execute("SELECT _source, _row FROM _excluded").fetchall()
    return {(str(source), int(row)) for source, row in rows}


def score(
    labels: dict[str, set[RowId]], predicted: dict[str, set[RowId]]
) -> dict[str, ExclusionScore]:
    result: dict[str, ExclusionScore] = {}
    for name in sorted(set(labels) | set(predicted)):
        truth, guess = labels.get(name, set()), predicted.get(name, set())
        result[name] = ExclusionScore(
            hits=len(truth & guess), missed=len(truth - guess), wrong=len(guess - truth)
        )
    result["total"] = ExclusionScore(
        hits=sum(s.hits for s in result.values()),
        missed=sum(s.missed for s in result.values()),
        wrong=sum(s.wrong for s in result.values()),
    )
    return result


def _percent(value: float | None) -> str:
    return "-" if value is None else f"{value:.1%}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Score excluded rows against labels.")
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--datasets", type=Path, required=True, help="one directory per dataset")
    args = parser.parse_args()

    names = sorted(p.name for p in args.datasets.iterdir() if (p / DATABASE).exists())
    labels = load_labels(args.labels, names)
    unbuilt = sorted(set(labels) - set(names))
    if unbuilt:
        print(f"labelled but not built, counted as missed: {unbuilt}")
    predicted = {name: excluded_rows(args.datasets / name / DATABASE) for name in names}
    print(f"{'dataset':<24} {'hits':>6} {'missed':>6} {'wrong':>6} {'precision':>9} {'recall':>7}")
    for name, s in score(labels, predicted).items():
        if name == "total" or s.hits or s.missed or s.wrong:
            print(
                f"{name:<24} {s.hits:>6} {s.missed:>6} {s.wrong:>6} "
                f"{_percent(s.precision):>9} {_percent(s.recall):>7}"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
