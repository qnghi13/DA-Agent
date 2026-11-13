"""The CSV writer behind result export: whole results or nothing (docs/design/2026-11-14)."""

import csv
from pathlib import Path

import duckdb
import pytest

from da_agent.storage.csv_export import ExportLimitError, write_csv


def _database(tmp_path: Path, *statements: str) -> Path:
    path = tmp_path / "dataset.duckdb"
    with duckdb.connect(str(path)) as connection:
        for statement in statements:
            connection.execute(statement)
    return path


def _rows(path: Path) -> list[list[str]]:
    with path.open(encoding="utf-8-sig", newline="") as file:
        return list(csv.reader(file))


def test_writes_a_bom_header_and_every_row(tmp_path: Path) -> None:
    database = _database(
        tmp_path,
        "CREATE TABLE shops (id INTEGER, name VARCHAR, amount INTEGER)",
        "INSERT INTO shops VALUES (1, 'Hà Nội', 10), (2, 'Huế', 20), (3, 'Đà Nẵng', 30)",
    )
    out = tmp_path / "result.csv"

    report = write_csv(database, "SELECT * FROM shops ORDER BY id", out)

    assert out.read_bytes().startswith(b"\xef\xbb\xbf")
    assert _rows(out) == [
        ["id", "name", "amount"],
        ["1", "Hà Nội", "10"],
        ["2", "Huế", "20"],
        ["3", "Đà Nẵng", "30"],
    ]
    assert report.rows == 3
    assert report.columns == ["id", "name", "amount"]
    assert report.bytes == out.stat().st_size


def test_dates_are_iso_and_numbers_have_no_separators(tmp_path: Path) -> None:
    database = _database(
        tmp_path,
        "CREATE TABLE t (d DATE, n INTEGER, x DECIMAL(10, 2), e INTEGER)",
        "INSERT INTO t VALUES (DATE '2026-06-01', 22000, 1234.50, NULL)",
    )
    out = tmp_path / "result.csv"

    write_csv(database, "SELECT * FROM t", out)

    assert _rows(out)[1] == ["2026-06-01", "22000", "1234.50", ""]


def test_formula_text_is_escaped_but_numbers_are_not(tmp_path: Path) -> None:
    database = _database(
        tmp_path,
        "CREATE TABLE t (v VARCHAR, n INTEGER)",
        "INSERT INTO t VALUES ('=1+1', -5), ('+84', 1), ('-5', 2), ('@x', 3), ('\tx', 4), "
        "('\rx', 5), ('plain', 6)",
    )
    out = tmp_path / "result.csv"

    write_csv(database, "SELECT v, n FROM t ORDER BY n", out)

    assert _rows(out)[1:] == [
        ["'=1+1", "-5"],
        ["'+84", "1"],
        ["'-5", "2"],
        ["'@x", "3"],
        ["'\tx", "4"],
        ["'\rx", "5"],
        ["plain", "6"],
    ]


def test_exactly_the_row_cap_is_exported(tmp_path: Path) -> None:
    database = _database(tmp_path, "CREATE TABLE t AS SELECT range AS id FROM range(10)")
    out = tmp_path / "result.csv"

    report = write_csv(database, "SELECT id FROM t", out, max_rows=10)

    assert report.rows == 10
    assert len(_rows(out)) == 11


def test_over_the_row_cap_fails_and_leaves_no_file(tmp_path: Path) -> None:
    database = _database(tmp_path, "CREATE TABLE t AS SELECT range AS id FROM range(11)")
    out = tmp_path / "result.csv"

    with pytest.raises(ExportLimitError, match="more than 10 rows"):
        write_csv(database, "SELECT id FROM t", out, max_rows=10)
    assert not out.exists()


def test_over_the_byte_cap_fails_and_leaves_no_file(tmp_path: Path) -> None:
    database = _database(tmp_path, "CREATE TABLE t AS SELECT range AS id FROM range(100)")
    out = tmp_path / "result.csv"

    with pytest.raises(ExportLimitError, match="over"):
        write_csv(database, "SELECT id FROM t", out, max_bytes=50)
    assert not out.exists()


def test_a_failing_query_leaves_no_file(tmp_path: Path) -> None:
    database = _database(tmp_path, "CREATE TABLE t (id INTEGER)")
    out = tmp_path / "result.csv"

    with pytest.raises(duckdb.Error):
        write_csv(database, "SELECT * FROM missing", out)
    assert not out.exists()


@pytest.mark.security
def test_a_query_cannot_read_files_outside_the_dataset(tmp_path: Path) -> None:
    secret = tmp_path / "secret.csv"
    secret.write_text("token\nSECRET-CANARY\n")
    database = _database(tmp_path, "CREATE TABLE t (id INTEGER)")
    out = tmp_path / "result.csv"

    with pytest.raises(duckdb.Error):
        write_csv(database, f"SELECT * FROM read_csv('{secret}')", out)  # noqa: S608 - the attack under test
    assert not out.exists()


@pytest.mark.security
def test_formula_column_names_are_escaped(tmp_path: Path) -> None:
    database = _database(tmp_path, "CREATE TABLE t (id INTEGER)", "INSERT INTO t VALUES (1)")
    out = tmp_path / "result.csv"

    report = write_csv(database, 'SELECT id AS "=HYPERLINK(1)", id AS "@n" FROM t', out)

    assert _rows(out)[0] == ["'=HYPERLINK(1)", "'@n"]
    assert report.columns == ["=HYPERLINK(1)", "@n"]


def test_binary_cells_are_written_as_hex(tmp_path: Path) -> None:
    database = _database(
        tmp_path, "CREATE TABLE t (b BLOB)", "INSERT INTO t VALUES ('\\xDE\\xAD'::BLOB)"
    )
    out = tmp_path / "result.csv"

    write_csv(database, "SELECT b FROM t", out)

    assert _rows(out)[1] == ["dead"]
