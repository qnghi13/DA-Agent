"""Values that could not be read at load time are empty in the clean table, so a query over them
leaves them out without saying so. The check finds them inside the rows a query reads."""

import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from da_agent.agent.data_checks import unreadable_problems
from da_agent.storage.sqlite_dataset import SqliteDataset


class LoadedDataset:
    """A built dataset: clean rows keep their source row, failed values sit in _quarantine, and
    describe() knows how many values of each column failed."""

    dialect = "SQLite"

    def __init__(self, path: Path) -> None:
        connection = sqlite3.connect(path)
        connection.executescript(
            """
            CREATE TABLE sales (order_date TEXT, total INTEGER, _source TEXT, _row INTEGER);
            INSERT INTO sales VALUES ('2025-03-05', 100, 'f', 1), ('2025-03-06', NULL, 'f', 2),
                                     ('2025-04-01', NULL, 'f', 3), ('2025-04-02', 50, 'f', 4);
            CREATE TABLE _quarantine (table_name TEXT, column_name TEXT, _source TEXT,
                                      _row INTEGER, raw_value TEXT);
            INSERT INTO _quarantine VALUES ('sales', 'total', 'f', 2, '-1tr040'),
                                           ('sales', 'total', 'f', 3, '-2tr');
            """
        )
        connection.commit()
        connection.close()
        self.inner = SqliteDataset(path)
        self.queries: list[str] = []

    def describe(self) -> list:
        tables = [t for t in self.inner.describe() if t.name == "sales"]
        return [
            replace(
                table,
                columns=[replace(c, unparsed=2) if c.name == "total" else c for c in table.columns],
            )
            for table in tables
        ]

    def query(self, sql: str):
        self.queries.append(sql)
        return self.inner.query(sql)


@pytest.fixture
def shop(tmp_path: Path) -> LoadedDataset:
    return LoadedDataset(tmp_path / "loaded.sqlite")


def test_unreadable_values_inside_the_query_are_reported(shop: LoadedDataset) -> None:
    sql = "SELECT sum(total) FROM sales WHERE order_date BETWEEN '2025-03-01' AND '2025-03-31'"

    found = unreadable_problems([sql], shop, "sqlite")

    assert len(found) == 1
    assert "sales.total" in found[0] and "1 " in found[0]


def test_unreadable_values_outside_the_query_do_not_matter(shop: LoadedDataset) -> None:
    sql = "SELECT sum(total) FROM sales WHERE order_date = '2025-04-02'"

    assert unreadable_problems([sql], shop, "sqlite") == []


def test_columns_that_read_cleanly_are_not_probed(shop: LoadedDataset) -> None:
    assert (
        unreadable_problems(["SELECT count(*), max(order_date) FROM sales"], shop, "sqlite") == []
    )
    assert shop.queries == []


def test_aliased_tables_are_followed(shop: LoadedDataset) -> None:
    sql = "SELECT s.order_date, s.total FROM sales s ORDER BY s.total DESC"

    found = unreadable_problems([sql], shop, "sqlite")

    assert len(found) == 1 and "2 " in found[0]


def test_a_probe_that_fails_is_not_a_problem(tmp_path: Path) -> None:
    path = tmp_path / "plain.sqlite"
    connection = sqlite3.connect(path)
    connection.executescript("CREATE TABLE sales (order_date TEXT, total INTEGER);")
    connection.close()
    plain = LoadedDataset.__new__(LoadedDataset)
    plain.inner, plain.queries = SqliteDataset(path), []

    assert unreadable_problems(["SELECT sum(total) FROM sales"], plain, "sqlite") == []
