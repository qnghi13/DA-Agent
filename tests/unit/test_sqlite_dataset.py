"""Read-only dataset behaviour: descriptions, bounded results, and reference tables."""

import sqlite3
from pathlib import Path

import pytest

from da_agent.storage.sqlite_dataset import QueryError, ReferenceTable, SqliteDataset


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "shop.sqlite"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE orders (id INTEGER, note TEXT)")
    connection.executemany("INSERT INTO orders VALUES (?, ?)", [(i, "x" * 500) for i in range(100)])
    connection.commit()
    connection.close()
    return path


def test_describe_lists_tables_columns_and_row_counts(db_path: Path) -> None:
    table = SqliteDataset(db_path).describe()[0]

    assert (table.name, table.row_count) == ("orders", 100)
    assert [(column.name, column.type) for column in table.columns] == [
        ("id", "INTEGER"),
        ("note", "TEXT"),
    ]


def test_query_returns_columns_and_rows(db_path: Path) -> None:
    result = SqliteDataset(db_path).query("SELECT id AS n FROM orders WHERE id < 2 ORDER BY id")

    assert result.columns == ["n"]
    assert result.rows == [[0], [1]]
    assert result.truncated is False


def test_query_limits_rows_and_says_so(db_path: Path) -> None:
    result = SqliteDataset(db_path, max_rows=10).query("SELECT id FROM orders")

    assert len(result.rows) == 10
    assert result.truncated is True


def test_query_limits_cell_size(db_path: Path) -> None:
    result = SqliteDataset(db_path, max_cell_chars=20).query("SELECT note FROM orders LIMIT 1")

    assert len(result.rows[0][0]) <= 21
    assert result.truncated is True


def test_large_integers_are_returned_exactly(db_path: Path) -> None:
    assert SqliteDataset(db_path).query("SELECT 3000000001").rows == [[3000000001]]


def test_bad_sql_becomes_a_query_error_naming_the_problem(db_path: Path) -> None:
    with pytest.raises(QueryError, match="missing_table"):
        SqliteDataset(db_path).query("SELECT * FROM missing_table")


def test_reference_tables_can_be_queried_and_joined(db_path: Path) -> None:
    calendar = ReferenceTable(columns=["id", "label"], rows=[[1, "Tet"], [2, "Normal"]])
    dataset = SqliteDataset(db_path, reference_tables={"calendar": calendar})

    result = dataset.query(
        "SELECT c.label FROM orders o JOIN calendar c ON o.id = c.id ORDER BY o.id"
    )

    assert result.rows == [["Tet"], ["Normal"]]
    assert "calendar" in {table.name for table in dataset.describe()}


def test_reference_tables_do_not_touch_the_file(db_path: Path) -> None:
    before = db_path.read_bytes()

    SqliteDataset(db_path, reference_tables={"calendar": ReferenceTable(["a"], [[1]])})

    assert db_path.read_bytes() == before


def test_missing_file_fails_clearly(tmp_path: Path) -> None:
    with pytest.raises(QueryError, match="absent"):
        SqliteDataset(tmp_path / "absent.sqlite")
