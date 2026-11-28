"""Scoring raw tables against hand-written layout targets."""

import json
from pathlib import Path

import duckdb

from eval.ingestion.layout_targets import LayoutTarget, load_targets, score_dataset


def raw_db(path: Path, tables: dict[str, list[dict]]) -> Path:
    """Each table is written as VARCHAR columns through a JSON file, as raw ingest does."""
    with duckdb.connect(str(path)) as connection:
        for name, rows in tables.items():
            source = path.parent / f"{name}.json"
            source.write_text(json.dumps(rows), encoding="utf-8")
            connection.execute(
                f"CREATE TABLE \"{name}\" AS SELECT * FROM read_json(?, format='array', "  # noqa: S608
                "auto_detect=false, columns=?)",
                [str(source), {column: "VARCHAR" for column in rows[0]}],
            )
    return path


def target(rows: int, samples: list[list[str]], source: str = "f.xlsx#S") -> LayoutTarget:
    return LayoutTarget(dataset="shop", source=source, rows=rows, samples=samples)


def test_targets_load_from_json_lines(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    line = {"dataset": "shop", "source": "f.xlsx#S", "rows": 2, "samples": [["x", "7"]]}
    path.write_text(json.dumps(line) + "\n\n", encoding="utf-8")

    assert load_targets(path) == [target(2, [["x", "7"]])]


def test_a_target_passes_when_rows_and_every_sample_match(tmp_path: Path) -> None:
    db = raw_db(
        tmp_path / "raw.duckdb",
        {
            "t": [
                {"_source": "f.xlsx#S", "_row": 2, "a": "x", "b": "y", "c": "7"},
                {"_source": "f.xlsx#S", "_row": 3, "a": "z", "b": None, "c": "8"},
            ]
        },
    )

    [result] = score_dataset(db, [target(2, [["x", " 7 "]])])

    assert result.found_rows == 2
    assert result.missing_samples == []
    assert result.passed


def test_samples_must_sit_in_one_row_not_across_rows(tmp_path: Path) -> None:
    db = raw_db(
        tmp_path / "raw.duckdb",
        {
            "t": [
                {"_source": "f.xlsx#S", "_row": 2, "a": "x", "c": "1"},
                {"_source": "f.xlsx#S", "_row": 3, "a": "z", "c": "7"},
            ]
        },
    )

    [result] = score_dataset(db, [target(2, [["x", "7"]])])

    assert result.missing_samples == [["x", "7"]]
    assert not result.passed


def test_rows_are_counted_across_tables_from_the_same_source(tmp_path: Path) -> None:
    db = raw_db(
        tmp_path / "raw.duckdb",
        {
            "left": [{"_source": "f.xlsx#S", "_row": 1, "a": "x"}],
            "right": [
                {"_source": "f.xlsx#S", "_row": 1, "k": "y"},
                {"_source": "other.csv", "_row": 1, "k": "x"},
            ],
        },
    )

    [result] = score_dataset(db, [target(2, [["x"], ["y"]])])

    assert result.found_rows == 2
    assert result.passed


def test_a_wrong_row_count_fails_even_when_samples_match(tmp_path: Path) -> None:
    db = raw_db(tmp_path / "raw.duckdb", {"t": [{"_source": "f.xlsx#S", "_row": 2, "a": "x"}]})

    [result] = score_dataset(db, [target(3, [["x"]])])

    assert result.found_rows == 1
    assert result.missing_samples == []
    assert not result.passed


def test_meta_columns_do_not_count_as_values(tmp_path: Path) -> None:
    db = raw_db(
        tmp_path / "raw.duckdb",
        {"t": [{"_source": "f.xlsx#S", "_row": 5, "_cell": "A5:B5", "a": "x"}]},
    )

    results = score_dataset(db, [target(1, [["x", "5"]]), target(1, [["A5:B5"]])])

    assert [r.missing_samples for r in results] == [[["x", "5"]], [["A5:B5"]]]


def test_values_match_after_unicode_normalization(tmp_path: Path) -> None:
    decomposed = "Tiếng"  # "Tiếng" in NFD
    db = raw_db(
        tmp_path / "raw.duckdb", {"t": [{"_source": "f.xlsx#S", "_row": 1, "a": decomposed}]}
    )

    [result] = score_dataset(db, [target(1, [["Tiếng"]])])

    assert result.passed
