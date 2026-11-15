"""Metric definitions read from a query, and matched against later queries (design
2026-11-15-metric-definitions). Everything comes from the syntax tree, so a definition never
claims a filter its query did not apply."""

import pytest
from da_agent.reports.metric import (
    Definition,
    definition_columns,
    metric_candidates,
    uses_metric,
)

from da_agent.reports.explain import Labels

LABELS = Labels(
    tables={"orders": "Đơn hàng"},
    columns={"orders": {"thanh_tien": "Thành tiền", "trang_thai": "Trạng thái"}},
)
VISIBLE = {
    "orders": {"thanh_tien", "trang_thai", "ngay", "san_pham", "nhom", "so_luong"},
    "items": {"order_id", "gia"},
}
REVENUE_SQL = (
    "SELECT SUM(thanh_tien) AS doanh_thu FROM orders WHERE trang_thai <> 'Hủy' "
    "AND ngay >= DATE '2026-05-01' AND ngay < DATE '2026-06-01'"
)


def revenue() -> Definition:
    [definition] = metric_candidates(REVENUE_SQL, LABELS, VISIBLE)
    return definition


def test_sum_with_where_gives_measure_and_filters() -> None:
    definition = revenue()
    assert definition.table == "orders"
    assert "thanh_tien" in definition.measure.lower()
    assert len(definition.filters) == 1
    assert "trang_thai" in definition.filters[0]
    assert all("ngay" not in f for f in definition.filters)
    assert definition.facts["measure"]["func"] == "sum"
    assert definition.facts["measure"]["of"]["column"] == "Thành tiền"
    [fact] = definition.facts["filters"]
    assert fact["op"] == "not_equals"
    assert fact["of"]["column"] == "Trạng thái"


@pytest.mark.parametrize(
    "measure",
    [
        "SUM(CASE WHEN trang_thai <> 'Hủy' THEN thanh_tien ELSE 0 END)",
        "SUM(thanh_tien) FILTER (WHERE trang_thai <> 'Hủy')",
    ],
)
def test_a_condition_inside_the_aggregate_becomes_a_filter(measure: str) -> None:
    [definition] = metric_candidates(f"SELECT {measure} FROM orders", LABELS, VISIBLE)  # noqa: S608
    assert definition.measure == revenue().measure
    assert definition.filters == revenue().filters
    assert definition.facts["measure"]["condition"] == []
    assert [f["op"] for f in definition.facts["filters"]] == ["not_equals"]


def test_rounding_does_not_change_the_measure() -> None:
    [definition] = metric_candidates(
        "SELECT ROUND(SUM(thanh_tien), 2) FROM orders WHERE trang_thai <> 'Hủy'", LABELS, VISIBLE
    )
    assert definition == revenue()


def test_each_aggregate_is_a_candidate_and_group_by_is_ignored() -> None:
    candidates = metric_candidates(
        "SELECT nhom, SUM(so_luong), COUNT(*) FROM orders GROUP BY nhom ORDER BY 2 DESC LIMIT 5",
        LABELS,
        VISIBLE,
    )
    assert len(candidates) == 2
    assert all(c.filters == [] for c in candidates)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT SUM(gia) FROM orders o JOIN items i ON i.order_id = o.id",
        "SELECT SUM(thanh_tien) FROM orders WHERE san_pham IN (SELECT san_pham FROM orders)",
        "WITH x AS (SELECT * FROM orders) SELECT SUM(thanh_tien) FROM x",
        "SELECT SUM(thanh_tien) OVER () FROM orders",
        "SELECT SUM(thanh_tien) FROM orders UNION ALL SELECT SUM(thanh_tien) FROM orders",
        "SELECT SUM(thanh_tien) FROM (SELECT * FROM orders)",
    ],
)
def test_no_candidate_from_join_subquery_cte_window_or_union(sql: str) -> None:
    assert metric_candidates(sql, LABELS, VISIBLE) == []


def test_column_not_visible_drops_the_candidate() -> None:
    sql = "SELECT SUM(thanh_tien), SUM(luong) FROM orders"
    [definition] = metric_candidates(sql, LABELS, VISIBLE)
    assert "thanh_tien" in definition.measure


def test_definition_columns() -> None:
    assert definition_columns(revenue()) == {("orders", "thanh_tien"), ("orders", "trang_thai")}


def test_round_trip_json() -> None:
    definition = revenue()
    assert Definition.from_json(definition.to_json()) == definition


def test_the_same_query_matches() -> None:
    assert uses_metric(REVENUE_SQL, revenue())


def test_extra_filters_still_match() -> None:
    sql = (
        "SELECT SUM(thanh_tien) FROM orders WHERE san_pham = 'Trà' AND trang_thai <> 'Hủy' "
        "AND ngay BETWEEN DATE '2026-06-01' AND DATE '2026-06-30'"
    )
    assert uses_metric(sql, revenue())


def test_equivalent_spelling_matches() -> None:
    sql = "SELECT round(sum(\"thanh_tien\"), 0) AS dt FROM orders o WHERE o.trang_thai != 'Hủy'"
    assert uses_metric(sql, revenue())


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT SUM(thanh_tien) FROM orders",
        "SELECT AVG(thanh_tien) FROM orders WHERE trang_thai <> 'Hủy'",
        "SELECT SUM(thanh_tien) FROM returns WHERE trang_thai <> 'Hủy'",
        "SELECT SUM(thanh_tien) FROM orders WHERE trang_thai <> 'Xong'",
    ],
)
def test_missing_filter_or_other_measure_or_table_does_not_match(sql: str) -> None:
    assert not uses_metric(sql, revenue())


@pytest.mark.parametrize(
    "measure",
    [
        "SUM(thanh_tien) FILTER (WHERE trang_thai <> 'Hủy')",
        "SUM(CASE WHEN trang_thai <> 'Hủy' THEN thanh_tien ELSE 0 END)",
        "SUM(thanh_tien) FILTER (WHERE trang_thai <> 'Hủy') / 1000",
    ],
)
def test_definition_filter_inside_the_aggregate_matches(measure: str) -> None:
    assert uses_metric(f"SELECT nhom, {measure} FROM orders GROUP BY nhom", revenue())  # noqa: S608


def test_count_one_and_count_star_are_the_same_measure() -> None:
    [definition] = metric_candidates("SELECT COUNT(1) FROM orders", LABELS, VISIBLE)
    assert uses_metric("SELECT count(*) FROM orders WHERE nhom = 'A'", definition)


def test_unparseable_sql_matches_nothing_and_gives_no_candidates() -> None:
    assert metric_candidates("SELEC nonsense", LABELS, VISIBLE) == []
    assert not uses_metric("SELEC nonsense", revenue())
