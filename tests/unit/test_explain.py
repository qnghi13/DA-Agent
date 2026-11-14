"""Plain explanations of SQL: facts read from the query, never more than it does.

See docs/design/2026-11-15-plain-explanation.md.
"""

from typing import Any

import pytest

from da_agent.agent.answer import AgentAnswer, AgentResult, QueryRecord
from da_agent.agent.verify import evidence_indices
from da_agent.reports import explain as explain_module
from da_agent.reports.explain import Labels, explain_result, explain_sql

LABELS = Labels(
    tables={"orders": "Đơn hàng", "customers": "Khách hàng"},
    columns={
        "orders": {"thanh_tien": "Thành tiền", "trang_thai": "Trạng thái", "ngay": "Ngày đặt"},
        "customers": {"ten": "Tên khách"},
    },
)


def col(name: str, unit: str | None = None) -> dict[str, Any]:
    return {"kind": "column", "column": name, "unit": unit}


def agg(func: str, of: dict[str, Any] | None, **extra: Any) -> dict[str, Any]:
    return {"kind": "aggregate", "func": func, "of": of, "distinct": False, "condition": []} | extra


def explain(sql: str) -> dict[str, Any]:
    return explain_sql(sql, LABELS).to_json()


def strip_names(item: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in item.items() if k != "name"}


def test_a_filtered_sum_uses_the_files_own_names() -> None:
    found = explain(
        "SELECT SUM(thanh_tien) AS revenue FROM orders "
        "WHERE trang_thai IN ('completed', 'Completed')"
    )

    assert found["tables"] == ["Đơn hàng"]
    assert found["measures"] == [agg("sum", col("Thành tiền")) | {"name": "revenue"}]
    assert found["filters"] == [
        {"of": col("Trạng thái"), "op": "in", "values": ["completed", "Completed"],
         "match": "exact"}
    ]  # fmt: skip
    assert found["complete"] is True and found["unexplained"] == []


def test_a_column_without_a_label_keeps_its_name() -> None:
    found = explain("SELECT SUM(phi_ship) FROM orders")

    assert strip_names(found["measures"][0]) == agg("sum", col("phi_ship"))


@pytest.mark.parametrize(
    ("select", "expected"),
    [
        ("COUNT(*)", agg("count", None)),
        ("COUNT(DISTINCT o.trang_thai)", agg("count", col("Trạng thái"), distinct=True)),
        ("AVG(thanh_tien)", agg("avg", col("Thành tiền"))),
        ("MAX(ngay)", agg("max", col("Ngày đặt"))),
        ("ROUND(SUM(thanh_tien), 2)", agg("sum", col("Thành tiền"))),
        ("COALESCE(SUM(thanh_tien), 0)", agg("sum", col("Thành tiền"))),
    ],
)
def test_aggregates(select: str, expected: dict[str, Any]) -> None:
    found = explain(f"SELECT {select} FROM orders o")  # noqa: S608

    assert [strip_names(m) for m in found["measures"]] == [expected]
    assert found["complete"] is True


def test_a_conditional_aggregate_keeps_its_condition() -> None:
    status = {"of": col("Trạng thái"), "op": "equals", "values": ["completed"],
              "match": "exact"}  # fmt: skip
    found = explain(
        "SELECT SUM(thanh_tien) FILTER (WHERE trang_thai = 'completed'), "
        "SUM(CASE WHEN trang_thai = 'completed' THEN thanh_tien ELSE 0 END), "
        "COUNT(CASE WHEN trang_thai = 'completed' THEN 1 END) FROM orders"
    )

    assert [strip_names(m) for m in found["measures"]] == [
        agg("sum", col("Thành tiền"), condition=[status]),
        agg("sum", col("Thành tiền"), condition=[status]),
        agg("count", None, condition=[status]),
    ]
    assert found["complete"] is True


def test_arithmetic_between_measures() -> None:
    found = explain("SELECT ROUND(SUM(thanh_tien) / COUNT(*) * 100, 1) AS share FROM orders")

    assert strip_names(found["measures"][0]) == {
        "kind": "arithmetic",
        "op": "multiply",
        "left": {"kind": "arithmetic", "op": "divide", "left": agg("sum", col("Thành tiền")),
                 "right": agg("count", None), "name": None},
        "right": {"kind": "number", "value": 100, "name": None},
    }  # fmt: skip


@pytest.mark.parametrize(
    ("where", "values"),
    [
        ("ngay BETWEEN '2025-06-01' AND '2025-06-30'", ["2025-06-01", "2025-06-30"]),
        ("ngay >= DATE '2025-06-01' AND ngay < DATE '2025-07-01'", ["2025-06-01", "2025-06-30"]),
        ("strftime(ngay, '%Y-%m') = '2025-06'", ["2025-06-01", "2025-06-30"]),
        ("ngay >= '2025-06-01'", ["2025-06-01", None]),
    ],
)
def test_date_conditions_become_one_period(where: str, values: list[Any]) -> None:
    found = explain(f"SELECT COUNT(*) FROM orders WHERE {where}")  # noqa: S608

    assert found["filters"] == [
        {"of": col("Ngày đặt"), "op": "period", "values": values, "match": "exact"}
    ]


@pytest.mark.parametrize(
    ("where", "op", "values", "match"),
    [
        ("trang_thai <> 'cancelled'", "not_equals", ["cancelled"], "exact"),
        ("trang_thai NOT IN ('cancelled', 'returned')", "not_in", ["cancelled", "returned"],
         "exact"),
        ("thanh_tien > 100", "greater", [100], "exact"),
        ("thanh_tien <= 100.5", "at_most", [100.5], "exact"),
        ("thanh_tien BETWEEN 10 AND 20", "between", [10, 20], "exact"),
        ("trang_thai LIKE '%huy%'", "contains", ["huy"], "exact"),
        ("lower(trang_thai) LIKE 'hoan%'", "starts_with", ["hoan"], "ignore_case"),
        ("lower(trang_thai) = 'completed'", "equals", ["completed"], "ignore_case"),
        ("trang_thai IS NULL", "is_blank", [], "exact"),
        ("trang_thai IS NOT NULL", "is_not_blank", [], "exact"),
        ("trang_thai = 'a' OR trang_thai = 'b'", "in", ["a", "b"], "exact"),
        ("100 < thanh_tien", "greater", [100], "exact"),
        ("strip_accents(lower(trang_thai)) LIKE '%huy%'", "contains", ["huy"],
         "ignore_case_and_accents"),
        ("trang_thai LIKE '%may%' OR trang_thai LIKE '%máy%'", "contains", ["may", "máy"],
         "exact"),
        ("thanh_tien > phi_ship", "greater", [col("phi_ship")], "exact"),
        ("da_tra", "is_true", [], "exact"),
        ("NOT da_tra", "is_false", [], "exact"),
    ],
)  # fmt: skip
def test_filters(where: str, op: str, values: list[Any], match: str) -> None:
    found = explain(f"SELECT COUNT(*) FROM orders WHERE {where}")  # noqa: S608

    assert found["filters"] == [
        {"of": col(found["filters"][0]["of"]["column"]), "op": op, "values": values,
         "match": match}
    ]  # fmt: skip
    assert found["complete"] is True


def test_grouping_ordering_and_top_n() -> None:
    found = explain(
        "SELECT date_trunc('month', ngay) AS thang, SUM(thanh_tien) AS revenue FROM orders "
        "GROUP BY thang ORDER BY revenue DESC LIMIT 5"
    )

    assert found["groups"] == [col("Ngày đặt", "month")]
    assert [strip_names(s) for s in found["shown"]] == [col("Ngày đặt", "month")]
    assert found["order"] == [{"by": agg("sum", col("Thành tiền")) | {"name": "revenue"},
                               "descending": True}]  # fmt: skip
    assert found["limit"] == 5


@pytest.mark.parametrize(
    ("group", "unit"),
    [
        ("strftime(ngay, '%Y-%m')", "month"),
        ("strftime('%Y-%m', ngay)", "month"),
        ("EXTRACT(hour FROM ngay)", "hour"),
        ("year(ngay)", "year"),
        ("month(ngay)", "month_of_year"),
        ("EXTRACT(month FROM ngay)", "month_of_year"),
        ("CAST(ngay AS DATE)", "day"),
        ("1", "month"),
    ],
)
def test_time_buckets(group: str, unit: str) -> None:
    found = explain(f"SELECT date_trunc('month', ngay), COUNT(*) FROM orders GROUP BY {group}")  # noqa: S608

    assert found["groups"] == [col("Ngày đặt", unit)]


def test_joins_resolve_aliases() -> None:
    found = explain(
        "SELECT c.ten, SUM(o.thanh_tien) FROM orders o "
        "LEFT JOIN customers c ON o.customer_id = c.id GROUP BY c.ten"
    )

    assert found["tables"] == ["Đơn hàng"]
    assert found["joins"] == [
        {"table": "Khách hàng", "kind": "left", "on": [["customer_id", "id"]]}
    ]
    assert found["groups"] == [col("Tên khách")]
    assert strip_names(found["measures"][0]) == agg("sum", col("Thành tiền"))


def test_having_filters_on_a_measure() -> None:
    found = explain(
        "SELECT trang_thai, COUNT(*) FROM orders GROUP BY trang_thai HAVING COUNT(*) > 10"
    )

    assert found["having"] == [
        {"of": agg("count", None), "op": "greater", "values": [10], "match": "exact"}
    ]


def test_distinct_rows_and_plain_columns() -> None:
    found = explain("SELECT DISTINCT trang_thai FROM orders")

    assert found["distinct_rows"] is True
    assert [strip_names(s) for s in found["shown"]] == [col("Trạng thái")]
    assert found["measures"] == []


@pytest.mark.parametrize(
    ("sql", "part"),
    [
        ("WITH a AS (SELECT * FROM orders) SELECT COUNT(*) FROM a", "with"),
        ("SELECT COUNT(*) FROM (SELECT * FROM orders) t", "subquery"),
        ("SELECT * FROM orders WHERE thanh_tien > (SELECT AVG(thanh_tien) FROM orders)",
         "subquery"),
        ("SELECT SUM(thanh_tien) OVER (ORDER BY ngay) FROM orders", "window"),
        ("SELECT 1 FROM orders UNION SELECT 2 FROM customers", "set_operation"),
        ("SELECT levenshtein(trang_thai, 'x') FROM orders", "expression"),
        ("SELECT COUNT(*) FROM orders WHERE trang_thai = 'a' OR thanh_tien > 5", "condition"),
        ("SELECT COUNT(*) FROM orders o JOIN customers c ON o.ten = c.ten OR o.id = c.id",
         "join"),
        ("SELEC nonsense", "parse"),
        ("SELECT 'open", "parse"),
    ],
)  # fmt: skip
def test_what_cannot_be_put_in_words_is_named_and_marks_it_incomplete(sql: str, part: str) -> None:
    found = explain(sql)

    assert part in found["unexplained"]
    assert found["complete"] is False


def test_labels_come_from_the_dataset_spec() -> None:
    spec = {
        "tables": [
            {"source": "Don hang.xlsx", "name": "orders",
             "columns": [{"source": "Thành tiền", "name": "thanh_tien"}]}
        ]
    }  # fmt: skip

    labels = Labels.from_spec(spec)

    assert labels == Labels(tables={"orders": "Don hang.xlsx"},
                            columns={"orders": {"thanh_tien": "Thành tiền"}})  # fmt: skip
    assert Labels.from_spec(None) == Labels()


def test_evidence_is_the_queries_holding_the_answers_numbers() -> None:
    queries = [
        QueryRecord(sql="SELECT 1", columns=["a"], rows=[[7]]),
        QueryRecord(sql="SELECT 2", columns=["a"], rows=[[42.0]]),
    ]

    assert evidence_indices(AgentAnswer(kind="number", value=42), queries) == [1]
    assert evidence_indices(AgentAnswer(kind="text", value="ok"), queries) == [1]
    assert evidence_indices(AgentAnswer(kind="number", value=1), []) == []


def test_a_reader_bug_marks_the_query_unexplained_instead_of_failing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(*_: Any) -> None:
        raise ValueError("reader bug")

    monkeypatch.setattr(explain_module._Reader, "read", broken)
    result = AgentResult(
        answer=AgentAnswer(kind="number", value=3),
        queries=[QueryRecord(sql="SELECT 3", columns=["n"], rows=[[3]])],
    )

    [found] = explain_result(result, Labels())

    assert found["query_index"] == 0
    assert found["unexplained"] == ["error"] and found["complete"] is False
