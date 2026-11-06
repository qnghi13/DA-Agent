"""Synthetic uploads with labelled subtotal, total, and note rows, built from no real data."""

from pathlib import Path

import duckdb

from da_agent.ingestion.raw import RAW_DATABASE, ingest
from eval.ingestion.synthetic import write_synthetic

MARKERS = ("subtotal", "grand total", "tổng", "cộng", "lưu ý")


def raw_rows(out: Path) -> dict[tuple[str, int], list[str]]:
    rows: dict[tuple[str, int], list[str]] = {}
    with duckdb.connect(str(out / RAW_DATABASE), read_only=True) as connection:
        for (table,) in connection.execute("SHOW TABLES").fetchall():
            # Table names come from SHOW TABLES on a file this test wrote.
            query = 'SELECT * FROM "{}"'
            for *cells, source, row in connection.execute(query.format(table)).fetchall():
                rows[(source, int(row))] = [c for c in cells if c is not None]
    return rows


def test_labels_mark_exactly_the_summary_and_note_rows(tmp_path: Path) -> None:
    labels = write_synthetic(tmp_path / "uploads")

    datasets = sorted(p.name for p in (tmp_path / "uploads").iterdir())
    assert len(datasets) >= 5
    for name in datasets:
        out = tmp_path / "raw" / name
        out.mkdir(parents=True)
        ingest(tmp_path / "uploads" / name, out)
        rows = raw_rows(out)
        labelled = {(label["source"], label["row"]) for label in labels if label["dataset"] == name}
        assert labelled <= set(rows), name
        marked = {
            key
            for key, cells in rows.items()
            if any(cell.strip().lower().startswith(MARKERS) for cell in cells)
        }
        assert labelled == marked, name


def test_one_dataset_is_clean_and_has_names_that_look_like_totals(tmp_path: Path) -> None:
    labels = write_synthetic(tmp_path / "uploads")

    labelled = {label["dataset"] for label in labels}
    clean = {p.name for p in (tmp_path / "uploads").iterdir()} - labelled
    assert clean


def test_the_output_is_the_same_every_time(tmp_path: Path) -> None:
    first = write_synthetic(tmp_path / "a")
    second = write_synthetic(tmp_path / "b")

    assert first == second
    for path in (tmp_path / "a").rglob("*.csv"):
        assert path.read_bytes() == (tmp_path / "b" / path.relative_to(tmp_path / "a")).read_bytes()
