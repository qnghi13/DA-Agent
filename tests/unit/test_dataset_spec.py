"""DatasetSpec schema and its checks against the raw report."""

import pytest
from pydantic import ValidationError

from da_agent.ingestion.report import ColumnProfile, ColumnReport, IngestReport, TableReport
from da_agent.ingestion.spec import ColumnSpec, DatasetSpec, Relationship, TableSpec, check_spec


def report() -> IngestReport:
    def table(name: str, columns: list[str]) -> TableReport:
        return TableReport(
            name=name,
            sources=[],
            row_count=1,
            columns=[ColumnReport(name=c, profile=ColumnProfile()) for c in [*columns, "_source"]],
        )

    return IngestReport(tables=[table("don", ["Mã đơn", "Ngày"]), table("kh", ["Mã KH"])])


def test_a_valid_spec_has_no_problems() -> None:
    spec = DatasetSpec(
        tables=[
            TableSpec(
                source="don",
                name="orders",
                columns=[
                    ColumnSpec(source="Mã đơn", name="order_id"),
                    ColumnSpec(source="Ngày", name="order_date", type="date", date_formats=["dmy"]),
                ],
                fill_down=["Mã đơn"],
            ),
            TableSpec(
                source="kh", name="customers", columns=[ColumnSpec(source="Mã KH", name="id")]
            ),
        ],
        relationships=[
            Relationship(
                from_table="orders", from_column="order_id", to_table="customers", to_column="id"
            )
        ],
    )

    assert check_spec(spec, report()) == []


@pytest.mark.parametrize("name", ["1x", "_x", "a b", "Tổng", 'x"; DROP TABLE y; --', "", "a" * 64])
def test_clean_names_must_be_plain_identifiers(name: str) -> None:
    with pytest.raises(ValidationError):
        ColumnSpec(source="x", name=name)


def test_unknown_fields_and_types_are_rejected() -> None:
    with pytest.raises(ValidationError):
        ColumnSpec.model_validate({"source": "x", "name": "x", "sql": "SELECT 1"})
    with pytest.raises(ValidationError):
        ColumnSpec.model_validate({"source": "x", "name": "x", "type": "python"})


def test_check_reports_problems_a_model_can_fix() -> None:
    spec = DatasetSpec(
        tables=[
            TableSpec(
                source="don",
                name="orders",
                columns=[
                    ColumnSpec(source="Mã", name="order_id"),
                    ColumnSpec(source="Ngày", name="order_id", type="date"),
                ],
                fill_down=["Nope"],
            ),
            TableSpec(source="ghost", name="ghost"),
        ],
        relationships=[
            Relationship(from_table="orders", from_column="x", to_table="kh", to_column="y")
        ],
    )

    problems = "\n".join(check_spec(spec, report()))

    assert "column 'Mã' is not in the raw table" in problems
    assert "'order_id' is used twice" in problems
    assert "fill_down column 'Nope'" in problems
    assert "table 'ghost' is not in the raw profile" in problems
    assert "unknown column orders.x" in problems


def test_lenient_parsing_keeps_valid_parts_and_lists_the_rest() -> None:
    from da_agent.ingestion.spec import parse_spec_lenient

    data = {
        "tables": [
            {
                "source": "don",
                "name": "orders",
                "columns": [
                    {"source": "Mã đơn", "name": "order_id"},
                    {"source": "Ngày", "name": "day", "type": "python"},
                ],
            },
            {"source": "kh", "name": "Bad Name"},
        ],
        "relationships": [{"from_table": "orders"}],
        "notes": ["n"],
    }

    spec, problems = parse_spec_lenient(data)

    assert [t.name for t in spec.tables] == ["orders"]
    assert [c.name for c in spec.tables[0].columns] == ["order_id"]
    assert spec.notes == ["n"]
    text = "\n".join(problems)
    assert "orders column 'Ngày'" in text and "type" in text
    assert "table 'kh'" in text
    assert "relationship 1" in text


def test_number_format_hints_are_not_part_of_the_spec() -> None:
    with pytest.raises(ValidationError):
        ColumnSpec.model_validate({"source": "x", "name": "x", "number_format": "comma_decimal"})


def test_day_first_and_month_first_cannot_share_a_column() -> None:
    column = ColumnSpec(source="Ngày", name="day", type="date", date_formats=["dmy", "mdy"])
    spec = DatasetSpec(tables=[TableSpec(source="don", name="orders", columns=[column])])

    assert any("both dmy and mdy" in problem for problem in check_spec(spec, report()))


def test_sanitize_drops_only_the_broken_parts() -> None:
    from da_agent.ingestion.spec import sanitize_spec

    spec = DatasetSpec(
        tables=[
            TableSpec(
                source="don",
                name="orders",
                columns=[
                    ColumnSpec(source="Mã đơn", name="order_id"),
                    ColumnSpec(source="Ghost", name="ghost"),
                    ColumnSpec(source="Ngày", name="order_id", type="date"),
                ],
                fill_down=["Mã đơn", "Nope"],
            ),
            TableSpec(source="nowhere", name="nowhere"),
        ],
        relationships=[
            Relationship(from_table="orders", from_column="order_id", to_table="x", to_column="y")
        ],
    )

    clean, problems = sanitize_spec(spec, report())

    assert [t.name for t in clean.tables] == ["orders"]
    assert [(c.source, c.name) for c in clean.tables[0].columns] == [
        ("Mã đơn", "order_id"),
        ("Ngày", "order_id_2"),
    ]
    assert clean.tables[0].fill_down == ["Mã đơn"]
    assert clean.relationships == []
    assert len(problems) == 5
    assert check_spec(clean, report()) == []


def test_sources_given_as_file_labels_map_to_their_table() -> None:
    from da_agent.ingestion.report import SourceReport
    from da_agent.ingestion.spec import sanitize_spec

    raw = report()
    raw.tables[0].sources = [
        SourceReport(source="Don.xlsx#Sheet1", header_row=1, skipped_rows=0, rows=1)
    ]
    spec = DatasetSpec(tables=[TableSpec(source="Don.xlsx#Sheet1", name="orders")])

    clean, problems = sanitize_spec(spec, raw)

    assert clean.tables[0].source == "don"
    assert problems == []


def test_unknown_date_formats_are_ignored_not_fatal() -> None:
    from da_agent.ingestion.spec import parse_spec_lenient

    column = {
        "source": "Ngày",
        "name": "day",
        "type": "datetime",
        "date_formats": ["julian", "dmy"],
    }
    data = {"tables": [{"source": "don", "name": "orders", "columns": [column]}]}

    spec, problems = parse_spec_lenient(data)

    assert spec.tables[0].columns[0].date_formats == ["dmy"]
    assert "'julian'" in problems[0]


def test_profile_pattern_names_are_read_as_date_formats() -> None:
    from da_agent.ingestion.spec import parse_spec_lenient

    formats = ["date_dmy", "datetime_iso", "date_text_vi"]
    column = {"source": "Ngày", "name": "day", "type": "date", "date_formats": formats}
    data = {"tables": [{"source": "don", "name": "orders", "columns": [column]}]}

    spec, problems = parse_spec_lenient(data)

    assert spec.tables[0].columns[0].date_formats == ["dmy", "vi_text"]
    assert problems == []


def test_text_conditions_need_a_value() -> None:
    from da_agent.ingestion.spec import RowCondition

    with pytest.raises(ValidationError):
        RowCondition(column="Ngày", op="starts_with")
    with pytest.raises(ValidationError):
        RowCondition(column="Ngày", op="matches", value=".*")
    assert RowCondition(column="Ngày", op="blank").value == ""


def test_exclusion_rules_must_name_raw_columns() -> None:
    from da_agent.ingestion.spec import RowCondition, RowRule, sanitize_spec

    rules = [
        RowRule(when=[RowCondition(column="Ngày", op="blank")], reason="ok"),
        RowRule(when=[RowCondition(column="Ghost", op="blank")], reason="ghost"),
    ]
    spec = DatasetSpec(tables=[TableSpec(source="don", name="orders", exclude_rows=rules)])

    assert any("'Ghost'" in problem for problem in check_spec(spec, report()))
    clean, problems = sanitize_spec(spec, report())
    assert [r.reason for r in clean.tables[0].exclude_rows] == ["ok"]
    assert len(problems) == 1 and "'Ghost'" in problems[0]


def test_a_broken_exclusion_rule_costs_only_that_rule() -> None:
    from da_agent.ingestion.spec import parse_spec_lenient

    rules = [
        {"when": [{"column": "Ngày", "op": "blank"}], "reason": "ok"},
        {"when": [{"column": "Ngày", "op": "regex", "value": "x"}], "reason": "bad"},
    ]
    data = {"tables": [{"source": "don", "name": "orders", "exclude_rows": rules}]}

    spec, problems = parse_spec_lenient(data)

    assert [r.reason for r in spec.tables[0].exclude_rows] == ["ok"]
    assert len(problems) == 1 and "exclude_rows" in problems[0]


def test_clean_column_names_in_row_rules_map_to_their_raw_columns() -> None:
    from da_agent.ingestion.spec import RowCondition, RowRule, sanitize_spec

    spec = DatasetSpec(
        tables=[
            TableSpec(
                source="don",
                name="orders",
                columns=[ColumnSpec(source="Mã đơn", name="order_id")],
                fill_down=["order_id"],
                exclude_rows=[RowRule(when=[RowCondition(column="order_id", op="blank")])],
            )
        ]
    )

    clean, problems = sanitize_spec(spec, report())

    assert clean.tables[0].fill_down == ["Mã đơn"]
    assert clean.tables[0].exclude_rows[0].when[0].column == "Mã đơn"
    assert problems == []


def test_columns_keep_source_names_that_are_already_identifiers() -> None:
    """Documents written for an upload use its own column names, so they stay when SQL can use
    them unquoted. Other names keep the spec's clean name."""
    from da_agent.ingestion.spec import keep_source_names

    spec = DatasetSpec(
        tables=[
            TableSpec(
                source="don",
                name="orders",
                columns=[
                    ColumnSpec(source="MaHoaDon", name="ma_hoa_don"),
                    ColumnSpec(source="Tổng tiền", name="total"),
                    ColumnSpec(source="ngay_lap", name="order_date", type="date"),
                    ColumnSpec(source="Order Id", name="order_id"),
                ],
            ),
            TableSpec(source="kh", name="kh", columns=[ColumnSpec(source="MaHoaDon", name="x")]),
        ],
        relationships=[
            Relationship(
                from_table="kh", from_column="x", to_table="orders", to_column="ma_hoa_don"
            )
        ],
    )

    kept = keep_source_names(spec)

    assert [c.name for c in kept.tables[0].columns] == ["MaHoaDon", "total", "ngay_lap", "order_id"]
    assert kept.relationships == [
        Relationship(from_table="kh", from_column="MaHoaDon", to_table="orders",
                     to_column="MaHoaDon")
    ]  # fmt: skip


def test_kept_source_names_never_collide_ignoring_case() -> None:
    """SQL matches unquoted names ignoring case, so TriGia and trigia would be one column."""
    from da_agent.ingestion.spec import keep_source_names

    spec = DatasetSpec(
        tables=[
            TableSpec(
                source="don",
                name="orders",
                columns=[
                    ColumnSpec(source="Tổng", name="trigia"),
                    ColumnSpec(source="TriGia", name="tri_gia"),
                    ColumnSpec(source="_row", name="row_copy"),
                ],
            )
        ]
    )

    kept = keep_source_names(spec)

    assert [c.name for c in kept.tables[0].columns] == ["trigia", "tri_gia", "row_copy"]


def test_sanitize_keeps_source_names() -> None:
    from da_agent.ingestion.spec import sanitize_spec

    raw = report()
    raw.tables[1].columns.insert(0, ColumnReport(name="MaKH", profile=ColumnProfile()))
    spec = DatasetSpec(
        tables=[TableSpec(source="kh", name="kh", columns=[ColumnSpec(source="MaKH", name="id")])]
    )

    clean, _ = sanitize_spec(spec, raw)

    assert clean.tables[0].columns[0].name == "MaKH"


@pytest.mark.parametrize(
    ("name", "fixed"),
    [("Tổng tiền phiếu", "tong_tien_phieu"), ("Số phiếu", "so_phieu"), ("1st day", "c_1st_day")],
)
def test_a_clean_name_written_as_raw_text_is_made_an_identifier(name: str, fixed: str) -> None:
    """Code can fix this for certain, so it costs neither the column nor a repair round."""
    from da_agent.ingestion.spec import parse_spec_lenient

    column = {"source": name, "name": name}
    data = {"tables": [{"source": "don", "name": "orders", "columns": [column]}]}

    spec, problems = parse_spec_lenient(data)

    assert [(c.source, c.name) for c in spec.tables[0].columns] == [(name, fixed)]
    assert problems == []


def test_rules_may_name_columns_the_spec_does_not_declare_by_their_default_clean_name() -> None:
    """Undeclared columns get slug names, and models write those names in fill_down and rules."""
    from da_agent.ingestion.spec import RowCondition, RowRule, sanitize_spec

    spec = DatasetSpec(
        tables=[
            TableSpec(
                source="don",
                name="orders",
                fill_down=["ma_don", "ngay"],
                exclude_rows=[
                    RowRule(when=[RowCondition(column="ma_don", op="starts_with", value="tổng")])
                ],
            )
        ]
    )

    clean, problems = sanitize_spec(spec, report())

    assert problems == []
    assert clean.tables[0].fill_down == ["Mã đơn", "Ngày"]
    assert clean.tables[0].exclude_rows[0].when[0].column == "Mã đơn"
    assert check_spec(clean, report()) == []
