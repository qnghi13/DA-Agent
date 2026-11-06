"""Scoring excluded rows against labels of rows that are not records."""

import json
from pathlib import Path

import duckdb

from eval.ingestion.exclusions import excluded_rows, load_labels, score


def write_labels(path: Path, rows: list[dict]) -> Path:
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    return path


def test_labels_are_grouped_by_dataset(tmp_path: Path) -> None:
    path = write_labels(
        tmp_path / "labels.jsonl",
        [
            {"dataset": "a", "source": "s.xlsx#S1", "row": 5, "kind": "subtotal"},
            {"dataset": "a", "source": "s.xlsx#S1", "row": 9, "kind": "note"},
            {"dataset": "b", "source": "t.csv", "row": 2, "kind": "total"},
        ],
    )

    labels = load_labels(path)

    assert labels == {"a": {("s.xlsx#S1", 5), ("s.xlsx#S1", 9)}, "b": {("t.csv", 2)}}


def test_excluded_rows_are_read_from_the_dataset(tmp_path: Path) -> None:
    database = tmp_path / "dataset.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute(
            "CREATE TABLE _excluded (table_name VARCHAR, reason VARCHAR, _source VARCHAR, "
            "_row BIGINT, raw_values VARCHAR)"
        )
        connection.execute("INSERT INTO _excluded VALUES ('t', 'total', 's.csv', 4, '{}')")

    assert excluded_rows(database) == {("s.csv", 4)}


def test_datasets_built_before_exclusion_existed_exclude_nothing(tmp_path: Path) -> None:
    database = tmp_path / "dataset.duckdb"
    duckdb.connect(str(database)).close()

    assert excluded_rows(database) == set()


def test_score_counts_hits_misses_and_wrong_exclusions() -> None:
    labels = {"a": {("s", 1), ("s", 2), ("s", 3), ("s", 4)}, "clean": set()}
    predicted = {"a": {("s", 1), ("s", 2), ("s", 3), ("s", 99)}, "clean": {("t", 7)}}

    result = score(labels, predicted)

    a, clean, total = result["a"], result["clean"], result["total"]
    assert (a.hits, a.missed, a.wrong) == (3, 1, 1)
    assert (a.precision, a.recall) == (0.75, 0.75)
    assert (clean.hits, clean.missed, clean.wrong) == (0, 0, 1)
    assert clean.recall is None and clean.precision == 0.0
    assert (total.hits, total.missed, total.wrong) == (3, 1, 2)


def test_a_dataset_without_predictions_misses_all_its_labels() -> None:
    result = score({"a": {("s", 1)}}, {})

    assert (result["a"].missed, result["a"].precision) == (1, None)
