"""The join fan-out check: a sum over rows that a join repeats counts them more than once."""

import sqlite3
from pathlib import Path

import pytest

from da_agent.agent.fanout import fanout_problems
from da_agent.storage.sqlite_dataset import SqliteDataset


class CountingDataset:
    """Records the probe queries the check runs."""

    def __init__(self, inner: SqliteDataset) -> None:
        self.inner = inner
        self.dialect = inner.dialect
        self.queries: list[str] = []

    def describe(self):
        return self.inner.describe()

    def query(self, sql: str):
        self.queries.append(sql)
        return self.inner.query(sql)


@pytest.fixture
def shop(tmp_path: Path) -> CountingDataset:
    path = tmp_path / "shop.sqlite"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE orders (id INTEGER, customer TEXT, total INTEGER);
        INSERT INTO orders VALUES (1, 'An', 100), (2, 'Binh', 250);
        CREATE TABLE lines (order_id INTEGER, product TEXT, amount INTEGER);
        INSERT INTO lines VALUES (1, 'tea', 40), (1, 'cake', 60), (2, 'tea', 250);
        CREATE TABLE payments (order_id INTEGER, paid INTEGER);
        INSERT INTO payments VALUES (1, 50), (1, 50), (2, 250);
        CREATE TABLE products (name TEXT, price INTEGER);
        INSERT INTO products VALUES ('tea', 10), ('cake', 20);
        CREATE TABLE returns (order_id INTEGER, reason TEXT);
        INSERT INTO returns VALUES (2, 'broken');
        """
    )
    connection.commit()
    connection.close()
    return CountingDataset(SqliteDataset(path))


def problems(shop: CountingDataset, sql: str) -> list[str]:
    return fanout_problems([sql], shop, "sqlite")


def test_a_sum_over_rows_the_join_repeats_is_rejected(shop: CountingDataset) -> None:
    found = problems(shop, "SELECT sum(o.total) FROM orders o JOIN lines l ON o.id = l.order_id")

    assert len(found) == 1
    assert "orders" in found[0]
    assert "3" in found[0] and "2" in found[0]


def test_a_sum_over_the_detail_side_passes(shop: CountingDataset) -> None:
    assert (
        problems(shop, "SELECT sum(l.amount) FROM orders o JOIN lines l ON o.id = l.order_id") == []
    )


def test_lookup_values_repeated_per_detail_row_pass(shop: CountingDataset) -> None:
    sql = "SELECT sum(l.amount * p.price) FROM lines l JOIN products p ON l.product = p.name"

    assert problems(shop, sql) == []


def test_filters_that_remove_the_repeats_pass(shop: CountingDataset) -> None:
    sql = "SELECT sum(o.total) FROM orders o JOIN lines l ON o.id = l.order_id WHERE o.id = 2"

    assert problems(shop, sql) == []


def test_unqualified_columns_are_found_by_the_schema(shop: CountingDataset) -> None:
    sql = "SELECT sum(total) FROM orders JOIN payments ON orders.id = payments.order_id"

    assert len(problems(shop, sql)) == 1


def test_aggregating_before_the_join_passes(shop: CountingDataset) -> None:
    sql = (
        "WITH paid AS (SELECT order_id, sum(paid) AS p FROM payments GROUP BY order_id) "
        "SELECT sum(o.total - paid.p) FROM orders o JOIN paid ON o.id = paid.order_id"
    )

    assert problems(shop, sql) == []


def test_a_repeating_join_inside_a_cte_is_found(shop: CountingDataset) -> None:
    sql = (
        "WITH per_customer AS (SELECT o.customer, sum(o.total) AS t FROM orders o "
        "JOIN payments p ON o.id = p.order_id GROUP BY o.customer) SELECT max(t) FROM per_customer"
    )

    assert len(problems(shop, sql)) == 1


@pytest.mark.parametrize("aggregate", ["count(DISTINCT o.id)", "max(o.total)", "min(o.total)"])
def test_aggregates_that_ignore_repeats_are_not_probed(
    shop: CountingDataset, aggregate: str
) -> None:
    sql = f"SELECT {aggregate} FROM orders o JOIN lines l ON o.id = l.order_id"  # noqa: S608

    assert problems(shop, sql) == []
    assert shop.queries == []


def test_queries_without_a_join_are_not_probed(shop: CountingDataset) -> None:
    assert problems(shop, "SELECT sum(total) FROM orders") == []
    assert shop.queries == []


def test_unmatched_rows_of_a_left_join_are_not_repeats(shop: CountingDataset) -> None:
    sql = "SELECT sum(o.total) FROM orders o LEFT JOIN returns r ON o.id = r.order_id"

    assert problems(shop, sql) == []


def test_a_probe_that_fails_is_not_a_problem(shop: CountingDataset) -> None:
    sql = "SELECT sum(o.total) FROM orders o JOIN missing m ON o.id = m.order_id"

    assert problems(shop, sql) == []


def test_unparsable_sql_is_not_a_problem(shop: CountingDataset) -> None:
    assert problems(shop, "SELEC nonsense ((") == []
