"""Attacks on the SQL path. Each test attempts the attack and proves that it fails."""

import sqlite3
from pathlib import Path

import pytest

from da_agent.security.sql_guard import SqlRejectedError, validate_select
from da_agent.storage.sqlite_dataset import QueryError, SqliteDataset

pytestmark = pytest.mark.security


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "shop.sqlite"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE customers (id TEXT, name TEXT, national_id TEXT);
        INSERT INTO customers VALUES ('C1', 'An', 'SECRET-111'), ('C2', 'Binh', 'SECRET-222');
        """
    )
    connection.commit()
    connection.close()
    return path


@pytest.fixture
def dataset(db_path: Path) -> SqliteDataset:
    return SqliteDataset(db_path, hidden_columns={"customers.national_id"}, timeout_seconds=0.3)


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE customers",
        "DELETE FROM customers",
        "UPDATE customers SET name = 'x'",
        "INSERT INTO customers VALUES ('a', 'b', 'c')",
        "CREATE TABLE stolen AS SELECT * FROM customers",
        "ATTACH DATABASE '/etc/passwd' AS other",
        "PRAGMA table_info(customers)",
        "SELECT 1; DROP TABLE customers",
        "SELECT 1; SELECT 2",
        "WITH x AS (SELECT 1) DELETE FROM customers",
        "SELECT load_extension('/tmp/evil.so')",
        "SELECT LOAD_EXTENSION('/tmp/evil.so')",
        "SELECT readfile('/etc/passwd')",
        "SELECT writefile('/tmp/x', 'y')",
        "",
        "   ",
        "-- only a comment",
        "not sql at all",
    ],
)
def test_guard_rejects_anything_but_one_safe_select(sql: str) -> None:
    with pytest.raises(SqlRejectedError):
        validate_select(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1",
        "select name from customers where id = 'C1';",
        "WITH x AS (SELECT 1 AS a) SELECT a FROM x",
        "SELECT 1 UNION ALL SELECT 2",
        "SELECT name, count(*) FROM customers GROUP BY name ORDER BY 2 DESC LIMIT 3",
        "SELECT strftime('%Y-%m', '2026-01-02')",
        "SELECT name FROM customers WHERE name LIKE '%DROP TABLE%'",
    ],
)
def test_guard_accepts_ordinary_queries(sql: str) -> None:
    assert validate_select(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT national_id FROM customers",
        "SELECT * FROM customers",
        "SELECT c.national_id AS safe_name FROM customers c",
        "SELECT x FROM (SELECT national_id AS x FROM customers)",
        "WITH t AS (SELECT national_id AS x FROM customers) SELECT x FROM t",
        "SELECT name FROM customers WHERE national_id LIKE 'SECRET-1%'",
        "SELECT name FROM customers ORDER BY national_id",
        "SELECT group_concat(national_id) FROM customers",
        'SELECT "NATIONAL_ID" FROM CUSTOMERS',
        "SELECT name FROM customers UNION SELECT national_id FROM customers",
    ],
)
def test_hidden_columns_cannot_be_read_or_probed(dataset: SqliteDataset, sql: str) -> None:
    with pytest.raises(QueryError):
        dataset.query(sql)


def test_hidden_columns_are_not_described(dataset: SqliteDataset) -> None:
    columns = {column.name for table in dataset.describe() for column in table.columns}

    assert "national_id" not in columns
    assert "name" in columns


def test_visible_columns_of_the_same_table_still_work(dataset: SqliteDataset) -> None:
    assert dataset.query("SELECT name FROM customers ORDER BY id").rows == [["An"], ["Binh"]]


def test_runaway_query_is_stopped(dataset: SqliteDataset) -> None:
    runaway = (
        "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) SELECT count(*) FROM c"
    )

    with pytest.raises(QueryError, match="time"):
        dataset.query(runaway)
    assert dataset.query("SELECT 1").rows == [[1]]


def test_dataset_cannot_change_the_file(db_path: Path, dataset: SqliteDataset) -> None:
    before = db_path.read_bytes()

    for sql in ("DROP TABLE customers", "CREATE TEMP TABLE t(x)"):
        with pytest.raises(QueryError):
            dataset.query(sql)

    assert db_path.read_bytes() == before
    assert sorted(path.name for path in db_path.parent.iterdir()) == ["shop.sqlite"]


def test_authorizer_blocks_what_gets_past_the_guard(db_path: Path) -> None:
    dataset = SqliteDataset(db_path, hidden_columns=frozenset())

    with pytest.raises(QueryError):
        dataset.query("SELECT load_extension('x')")
    with pytest.raises(QueryError):
        dataset._execute_unchecked("ATTACH DATABASE ':memory:' AS other")
    with pytest.raises(QueryError):
        dataset._execute_unchecked("DELETE FROM customers")
