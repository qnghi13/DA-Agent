"""Matching, merging, and repairing dataset versions: pure code over reports and specs."""

from da_agent.ingestion.build import BuildReport, ColumnBuild, ExcludedRule, TableBuild
from da_agent.ingestion.report import (
    ColumnProfile,
    ColumnReport,
    IngestReport,
    SourceReport,
    TableReport,
)
from da_agent.ingestion.spec import (
    ColumnSpec,
    DatasetSpec,
    OverlapDrop,
    Relationship,
    RowCondition,
    RowRule,
    TableSpec,
)
from da_agent.ingestion.versioning import (
    RepairChange,
    TableHeader,
    apply_repairs,
    headers,
    match_tables,
    merge_new_tables,
    quality_problems,
    rename_sources,
    repair_diff,
)

ORDER_HEADER = ("id", "total", "day")


def raw_table(name: str, header: tuple[str, ...], *sources: str) -> TableReport:
    return TableReport(
        name=name,
        sources=[SourceReport(source=s, header_row=1, skipped_rows=0, rows=2) for s in sources],
        row_count=2 * len(sources),
        columns=[
            ColumnReport(name=c, profile=ColumnProfile(non_null=2))
            for c in ("_source", "_row", *header)
        ],
    )


def spec_with(*tables: TableSpec, relationships: list[Relationship] | None = None) -> DatasetSpec:
    return DatasetSpec(tables=list(tables), relationships=relationships or [])


def orders_spec() -> TableSpec:
    return TableSpec(
        source="orders",
        name="orders",
        columns=[
            ColumnSpec(source="id", name="id"),
            ColumnSpec(source="total", name="total", type="money"),
            ColumnSpec(source="day", name="day", type="date", date_formats=["dmy"]),
        ],
    )


def test_headers_exclude_meta_columns() -> None:
    report = IngestReport(tables=[raw_table("orders", ORDER_HEADER, "orders.csv")])
    assert headers(report) == [TableHeader(raw_name="orders", header=ORDER_HEADER)]


def test_match_new_file_that_renames_group() -> None:
    previous = [TableHeader(raw_name="orders", header=ORDER_HEADER)]
    current = IngestReport(
        tables=[raw_table("a_orders", ORDER_HEADER, "a_orders.csv", "orders.csv")]
    )
    spec = spec_with(orders_spec())
    changes = match_tables(previous, current, frozenset({"a_orders.csv"}), spec)
    assert len(changes) == 1
    change = changes[0]
    assert (change.kind, change.raw_name, change.previous_raw_name, change.table) == (
        "grown",
        "a_orders",
        "orders",
        "orders",
    )
    renamed = rename_sources(spec, changes)
    assert renamed.tables[0].source == "a_orders"
    assert renamed.tables[0].columns == spec.tables[0].columns


def test_unchanged_table_when_no_new_source() -> None:
    previous = [TableHeader(raw_name="orders", header=ORDER_HEADER)]
    current = IngestReport(tables=[raw_table("orders", ORDER_HEADER, "orders.csv")])
    changes = match_tables(previous, current, frozenset({"other.csv"}), spec_with(orders_spec()))
    assert [c.kind for c in changes] == ["unchanged"]


def test_xlsx_with_matching_and_new_sheet() -> None:
    previous = [TableHeader(raw_name="orders", header=ORDER_HEADER)]
    current = IngestReport(
        tables=[
            raw_table("orders", ORDER_HEADER, "shop.csv", "more.xlsx#Orders"),
            raw_table("returns", ("ref", "reason"), "more.xlsx#Returns"),
        ]
    )
    changes = match_tables(
        previous,
        current,
        frozenset({"more.xlsx"}),
        spec_with(orders_spec()),
    )
    by_name = {c.raw_name: c for c in changes}
    assert by_name["orders"].kind == "grown"
    assert by_name["returns"].kind == "new"
    assert by_name["returns"].table is None
    assert by_name["returns"].near_match is None


def test_new_xlsx_sheet_with_existing_header_is_grown() -> None:
    previous = [TableHeader(raw_name="orders", header=ORDER_HEADER)]
    current = IngestReport(
        tables=[raw_table("orders", ORDER_HEADER, "orders.csv", "dec.xlsx#Sheet1")]
    )
    changes = match_tables(previous, current, frozenset({"dec.xlsx"}), spec_with(orders_spec()))
    assert [c.kind for c in changes] == ["grown"]
    # A sheet of an old workbook is not new.
    old = match_tables(previous, current, frozenset({"dec"}), spec_with(orders_spec()))
    assert [c.kind for c in old] == ["unchanged"]


def test_near_match_is_new_with_differences() -> None:
    previous = [TableHeader(raw_name="orders", header=ORDER_HEADER)]
    header = (*ORDER_HEADER, "discount")
    current = IngestReport(tables=[raw_table("orders_2", header, "b.csv")])
    changes = match_tables(previous, current, frozenset({"b.csv"}), spec_with(orders_spec()))
    new = next(c for c in changes if c.raw_name == "orders_2")
    assert new.kind == "new"
    assert new.near_match == "orders"
    assert new.extra_columns == ["discount"]
    assert new.missing_columns == []
    # The old table has no match now, so it is removed.
    assert any(c.kind == "removed" and c.raw_name == "orders" for c in changes)


def test_removed_table() -> None:
    previous = [
        TableHeader(raw_name="orders", header=ORDER_HEADER),
        TableHeader(raw_name="stock", header=("sku", "qty")),
    ]
    current = IngestReport(tables=[raw_table("orders", ORDER_HEADER, "orders.csv")])
    stock = TableSpec(source="stock", name="stock", columns=[ColumnSpec(source="sku", name="sku")])
    link = Relationship(from_table="stock", from_column="sku", to_table="orders", to_column="id")
    spec = spec_with(orders_spec(), stock, relationships=[link])
    changes = match_tables(previous, current, frozenset(), spec)
    removed = next(c for c in changes if c.kind == "removed")
    assert (removed.raw_name, removed.table) == ("stock", "stock")
    result = rename_sources(spec, changes)
    assert [t.name for t in result.tables] == ["orders"]
    assert result.relationships == []


def test_rename_keeps_table_spec_otherwise() -> None:
    table = orders_spec().model_copy(
        update={"drop_overlap": [OverlapDrop(file="b.csv", against=["a.csv"])]}
    )
    previous = [TableHeader(raw_name="orders", header=ORDER_HEADER)]
    current = IngestReport(tables=[raw_table("a_orders", ORDER_HEADER, "a_orders.csv")])
    spec = spec_with(table)
    result = rename_sources(spec, match_tables(previous, current, frozenset(), spec))
    assert result.tables[0] == table.model_copy(update={"source": "a_orders"})


def test_merge_keeps_existing_tables_exactly() -> None:
    current = spec_with(orders_spec())
    edited = orders_spec().model_copy(update={"description": "changed", "columns": []})
    new = TableSpec(
        source="returns",
        name="returns",
        columns=[ColumnSpec(source="ref", name="ref")],
        drop_overlap=[OverlapDrop(file="x.csv", against=["y.csv"])],
    )
    merged = merge_new_tables(current, spec_with(edited, new), {"returns"})
    assert merged.tables[0] == current.tables[0]
    assert [t.name for t in merged.tables] == ["orders", "returns"]
    assert merged.tables[1].drop_overlap == []


def test_merge_ignores_proposed_tables_outside_new_raw() -> None:
    other = TableSpec(source="stock", name="stock")
    merged = merge_new_tables(spec_with(orders_spec()), spec_with(other), {"returns"})
    assert [t.name for t in merged.tables] == ["orders"]


def test_merge_drops_relationships_between_old_tables() -> None:
    stock = TableSpec(source="stock", name="stock", columns=[ColumnSpec(source="sku", name="sku")])
    returns = TableSpec(
        source="returns", name="returns", columns=[ColumnSpec(source="id", name="id")]
    )
    current = spec_with(orders_spec(), stock)
    between_old = Relationship(
        from_table="stock", from_column="sku", to_table="orders", to_column="id"
    )
    to_new = Relationship(from_table="returns", from_column="id", to_table="orders", to_column="id")
    dangling = Relationship(
        from_table="returns", from_column="id", to_table="ghost", to_column="id"
    )
    proposed = spec_with(
        orders_spec(), stock, returns, relationships=[between_old, to_new, dangling]
    )
    merged = merge_new_tables(current, proposed, {"returns"})
    assert merged.relationships == [to_new]


def failing_proposal(**column_update) -> tuple[DatasetSpec, DatasetSpec]:
    current = spec_with(orders_spec())
    table = orders_spec()
    columns = [
        c.model_copy(update=column_update) if c.source == "day" else c for c in table.columns
    ]
    return current, spec_with(table.model_copy(update={"columns": columns}))


def test_repair_diff_accepts_failing_column_fields() -> None:
    current, proposed = failing_proposal(date_formats=["dmy", "ymd"])
    changes = repair_diff(current, proposed, {"orders": {"columns.day"}})
    assert changes == [
        RepairChange(
            table="orders", path="columns.day.date_formats", before=["dmy"], after=["dmy", "ymd"]
        )
    ]


def test_repair_diff_rejects_other_fields() -> None:
    current, proposed = failing_proposal(type="datetime")
    # Not failing: the column is left out.
    assert repair_diff(current, proposed, {"orders": {"columns.total"}}) == []
    assert repair_diff(current, proposed, {}) == []
    table = current.tables[0].model_copy(
        update={"name": "renamed", "description": "x", "fill_down": ["id"]}
    )
    assert repair_diff(current, spec_with(table), {"orders": {"columns.day"}}) == []
    column = current.tables[0].columns[0].model_copy(update={"name": "other", "description": "d"})
    renamed = current.tables[0].model_copy(
        update={"columns": [column, *current.tables[0].columns[1:]]}
    )
    assert repair_diff(current, spec_with(renamed), {"orders": {"columns.id"}}) == []


def test_repair_diff_exclude_rows_only_when_failing() -> None:
    rule = RowRule(when=[RowCondition(column="id", op="blank")], reason="blank")
    wider = RowRule(when=[RowCondition(column="id", op="equals", value="TOTAL")], reason="total")
    current = spec_with(orders_spec().model_copy(update={"exclude_rows": [rule]}))
    proposed = spec_with(orders_spec().model_copy(update={"exclude_rows": [wider]}))
    assert repair_diff(current, proposed, {"orders": {"columns.id"}}) == []
    changes = repair_diff(current, proposed, {"orders": {"exclude_rows.1"}})
    assert [(c.path, c.after) for c in changes] == [("exclude_rows.1", wider)]


def test_apply_repairs_changes_only_accepted() -> None:
    rule = RowRule(when=[RowCondition(column="id", op="blank")], reason="blank")
    spec = spec_with(orders_spec().model_copy(update={"exclude_rows": [rule]}))
    accepted = [
        RepairChange(table="orders", path="columns.day.type", before="date", after="datetime"),
        RepairChange(table="orders", path="columns.total.true_values", before=[], after=["x"]),
        RepairChange(table="orders", path="exclude_rows.1", before=rule, after=None),
    ]
    result = apply_repairs(spec, accepted)
    columns = {c.source: c for c in result.tables[0].columns}
    assert columns["day"].type == "datetime"
    assert columns["day"].date_formats == ["dmy"]
    assert columns["total"].true_values == ["x"]
    assert columns["id"] == spec.tables[0].columns[0]
    assert result.tables[0].exclude_rows == []
    assert spec.tables[0].columns[2].type == "date"


def column_build(source: str, non_blank: int, parsed: int) -> ColumnBuild:
    return ColumnBuild(name=source, source=source, type="date", non_blank=non_blank, parsed=parsed)


def test_quality_problems_new_rows_only() -> None:
    old = column_build("day", 100, 100)
    new = column_build("day", 10, 5)
    grown = TableBuild(
        name="orders", source="orders", rows=110, new_rows=10, columns=[old], new_columns=[new]
    )
    other = TableBuild(name="stock", source="stock", rows=5, columns=[old], new_columns=[])
    report = BuildReport(tables=[grown, other])
    problems = quality_problems(report, {"orders"})
    assert list(problems) == ["orders"]
    assert [p.key for p in problems["orders"]] == ["columns.day"]
    assert "50%" in problems["orders"][0].message
    # Only the grown set is judged.
    assert quality_problems(report, {"stock"}) == {}


def test_quality_problems_exclude_rule_on_new_rows() -> None:
    rule = ExcludedRule(reason="blank id", position=2, rows=6, full_rows=0)
    table = TableBuild(
        name="orders",
        source="orders",
        rows=104,
        raw_rows=200,
        new_rows=14,
        columns=[],
        new_columns=[],
        excluded=[ExcludedRule(reason="blank id", position=2, rows=6)],
        new_excluded=[rule],
    )
    problems = quality_problems(BuildReport(tables=[table]), {"orders"})
    assert [p.key for p in problems["orders"]] == ["exclude_rows.2"]
    assert "blank id" in problems["orders"][0].message
