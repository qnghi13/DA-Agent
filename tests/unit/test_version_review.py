"""What a version check reports and how the user's choices change the spec: pure code."""

import pytest

from da_agent.ingestion.build import BuildReport, ColumnBuild, Overlap, TableBuild
from da_agent.ingestion.report import (
    ColumnProfile,
    ColumnReport,
    IngestReport,
    SourceReport,
    TableReport,
)
from da_agent.ingestion.spec import ColumnSpec, DatasetSpec, OverlapDrop, TableSpec
from da_agent.ingestion.version_review import (
    blocking_tables,
    cover_raw_tables,
    pin_kept_tables,
    replaced_files,
    stale_metrics,
    version_changes,
    with_overlap_drops,
)
from da_agent.ingestion.versioning import QualityProblem, TableChange, TableHeader

HEADER = ("id", "total")


def raw_table(name: str, *sources: str, header: tuple[str, ...] = HEADER) -> TableReport:
    return TableReport(
        name=name,
        sources=[SourceReport(source=s, header_row=1, skipped_rows=0, rows=2) for s in sources],
        row_count=2 * len(sources),
        columns=[
            ColumnReport(name=c, profile=ColumnProfile(non_null=2))
            for c in ("_source", "_row", *header)
        ],
    )


def orders(**fields: object) -> TableSpec:
    values: dict[str, object] = {
        "source": "orders",
        "name": "orders",
        "columns": [ColumnSpec(source="total", name="total", type="money")],
    }
    return TableSpec.model_validate(values | fields)


def test_a_kept_table_without_a_spec_entry_keeps_its_name_when_its_group_is_renamed() -> None:
    # No spec entry: the build named the table after its raw group, `orders`. A new file that
    # sorts first renames the group to `a_orders`; the table must still be called `orders`.
    current = DatasetSpec(tables=[])
    previous = [TableHeader(raw_name="orders", header=HEADER)]
    changes = [
        TableChange(raw_name="a_orders", kind="grown", previous_raw_name="orders"),
    ]

    pinned = pin_kept_tables(current, current, previous, changes)

    assert pinned.tables == [TableSpec(source="a_orders", name="orders")]


def test_pinning_leaves_listed_tables_as_they_are() -> None:
    current = DatasetSpec(tables=[orders()])
    renamed = DatasetSpec(tables=[orders(source="a_orders")])
    previous = [TableHeader(raw_name="orders", header=HEADER)]
    changes = [TableChange(raw_name="a_orders", kind="grown", previous_raw_name="orders")]

    assert pin_kept_tables(renamed, current, previous, changes) == renamed


def test_every_raw_table_gets_a_spec_entry_named_as_the_build_names_it() -> None:
    spec = DatasetSpec(tables=[orders()])
    report = IngestReport(tables=[raw_table("orders", "orders.csv"), raw_table("Khách", "k.csv")])

    covered, added = cover_raw_tables(spec, report)

    assert covered.tables[0] == orders()
    assert covered.tables[1] == TableSpec(source="Khách", name="khach")
    assert added == ["khach"]


def test_an_exact_overlap_on_a_deduplicated_table_is_reported_but_not_asked() -> None:
    exact = Overlap(kind="exact", rows=2)
    build = BuildReport(tables=[TableBuild(name="orders", source="orders", overlaps=[exact])])
    changes = [TableChange(raw_name="orders", kind="grown", previous_raw_name="orders")]

    entries = version_changes(
        changes, DatasetSpec(tables=[orders(dedupe="exact")]), {}, build, {}, {}
    )

    assert entries[0]["overlaps"] == [
        {"kind": "exact", "rows": 2, "key": [], "column": None, "examples": [],
         "removed_by_dedupe": True}
    ]  # fmt: skip
    assert blocking_tables(entries) == set()


def test_version_changes_have_one_entry_per_table() -> None:
    key = Overlap(kind="key", rows=1, key=["id"])
    build = BuildReport(
        tables=[
            TableBuild(name="orders", source="orders", new_rows=3, overlaps=[key]),
            TableBuild(name="orders_v2", source="orders_v2", new_rows=4),
        ]
    )
    changes = [
        TableChange(raw_name="orders", kind="grown", previous_raw_name="orders"),
        TableChange(
            raw_name="orders_v2", kind="new", near_match="customers",
            missing_columns=["total"], extra_columns=["amount"],
        ),
        TableChange(raw_name="customers", kind="removed", previous_raw_name="customers"),
    ]  # fmt: skip
    spec = DatasetSpec(tables=[orders(), TableSpec(source="orders_v2", name="orders_v2")])
    quality = {"orders": [QualityProblem("columns.total", "orders.total: parsed 50%")]}

    entries = version_changes(
        changes, spec, {"customers": "clients"}, build, quality, {"clients": ["Revenue"]}
    )

    assert entries == [
        {
            "table": "orders", "kind": "grown", "new_rows": 3,
            "overlaps": [key.model_dump(mode="json")], "near_match": None,
            "missing_columns": [], "extra_columns": [],
            "quality": ["orders.total: parsed 50%"], "stale_metrics": [],
        },
        {
            "table": "orders_v2", "kind": "new", "new_rows": 4, "overlaps": [],
            "near_match": "clients", "missing_columns": ["total"], "extra_columns": ["amount"],
            "quality": [], "stale_metrics": [],
        },
        {
            "table": "clients", "kind": "removed", "new_rows": 0, "overlaps": [],
            "near_match": None, "missing_columns": [], "extra_columns": [], "quality": [],
            "stale_metrics": ["Revenue"],
        },
    ]  # fmt: skip
    assert blocking_tables(entries) == {"orders"}


def test_a_metric_on_a_removed_table_turns_stale() -> None:
    revenue = {"table": "orders", "measure": "sum(total)", "filters": []}
    count = {"table": "customers", "measure": "count(*)", "filters": []}
    built = BuildReport(
        tables=[
            TableBuild(
                name="customers",
                source="customers",
                columns=[ColumnBuild(name="name", source="name", type="text")],
            )
        ]
    )

    stale = stale_metrics([("Revenue", revenue), ("Customers", count)], {"orders"}, built)

    assert stale == {"orders": ["Revenue"]}


def test_a_metric_survives_when_a_new_table_takes_the_removed_name_with_its_columns() -> None:
    revenue = {"table": "orders", "measure": "sum(total)", "filters": []}
    column = ColumnBuild(name="total", source="total", type="money")
    built = BuildReport(tables=[TableBuild(name="orders", source="orders_v2", columns=[column])])

    assert stale_metrics([("Revenue", revenue)], {"orders"}, built) == {}


def test_drop_overlap_names_each_new_file_against_the_older_files_of_its_table() -> None:
    report = IngestReport(
        tables=[raw_table("orders", "orders.csv", "orders_2.csv", "orders_3.xlsx#Sheet1")]
    )
    spec = DatasetSpec(tables=[orders()])

    changed = with_overlap_drops(
        spec, {"orders"}, report, {"orders_2.csv", "orders_3.xlsx"}, {"orders": ["id"]}
    )

    assert changed.tables[0].drop_overlap == [
        OverlapDrop(file="orders_2.csv", against=["orders.csv"], key=["id"]),
        OverlapDrop(file="orders_3.xlsx", against=["orders.csv"], key=["id"]),
    ]
    assert changed.tables[0].columns == orders().columns


def test_drop_overlap_without_a_key_compares_whole_rows_and_keeps_earlier_drops() -> None:
    earlier = OverlapDrop(file="orders_1.csv", against=["orders.csv"])
    report = IngestReport(tables=[raw_table("orders", "orders.csv", "orders_1.csv", "z.csv")])
    spec = DatasetSpec(tables=[orders(drop_overlap=[earlier])])

    changed = with_overlap_drops(spec, {"orders"}, report, {"z.csv"}, {})

    assert changed.tables[0].drop_overlap == [
        earlier,
        OverlapDrop(file="z.csv", against=["orders.csv", "orders_1.csv"], key=[]),
    ]


def test_tables_without_a_drop_choice_are_untouched() -> None:
    report = IngestReport(tables=[raw_table("orders", "orders.csv", "orders_2.csv")])
    spec = DatasetSpec(tables=[orders()])

    assert with_overlap_drops(spec, set(), report, {"orders_2.csv"}, {}) == spec


def test_only_a_file_of_the_current_version_can_be_replaced() -> None:
    current = [{"name": "orders.csv", "stored_as": "orders.csv"}]
    chosen = {"orders": "replace", "items": "keep_both"}

    assert replaced_files(chosen, {"orders": "orders.csv"}, current) == {"orders.csv"}
    for names in ({}, {"orders": "orders_2.csv"}, {"orders": "../x.csv"}, {"orders": 3}):
        with pytest.raises(ValueError):
            replaced_files(chosen, names, current)
