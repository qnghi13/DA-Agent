"""Building clean tables from raw tables and a DatasetSpec, with parse rates and quarantine."""

import datetime as dt
import json
from pathlib import Path

import duckdb
import pytest

from da_agent.ingestion.build import CLEAN_DATABASE, build
from da_agent.ingestion.raw import RAW_DATABASE, ingest
from da_agent.ingestion.spec import ColumnSpec, DatasetSpec, RowCondition, RowRule, TableSpec


def raw_upload(tmp_path: Path, files: dict[str, str]) -> tuple[Path, object]:
    upload, out = tmp_path / "upload", tmp_path / "raw"
    upload.mkdir()
    out.mkdir()
    for name, text in files.items():
        (upload / name).write_text(text, encoding="utf-8")
    return out, ingest(upload, out)


def rows(out: Path, sql: str) -> list[tuple]:
    with duckdb.connect(str(out / CLEAN_DATABASE), read_only=True) as connection:
        return connection.execute(sql).fetchall()


def one_table(column: ColumnSpec, **table: object) -> DatasetSpec:
    return DatasetSpec(tables=[TableSpec(source="t", name="t", columns=[column], **table)])


@pytest.fixture
def out(tmp_path: Path) -> Path:
    path = tmp_path / "clean"
    path.mkdir()
    return path


def test_numbers_in_local_formats(tmp_path: Path, out: Path) -> None:
    values = ["22,000", "25000k", "578.000đ", "1.5tr", "2,5", "-3", "1 234", "abc", ""]
    raw, report = raw_upload(tmp_path, {"t.csv": "v\n" + "\n".join(f'"{v}"' for v in values)})

    result = build(
        raw / RAW_DATABASE, one_table(ColumnSpec(source="v", name="v", type="number")), report, out
    )

    assert [r[0] for r in rows(out, "SELECT v FROM t ORDER BY _row")] == [
        22000,
        25_000_000,
        578000,
        1_500_000,
        2.5,
        -3,
        1234,
        None,
    ]
    column = result.tables[0].columns[0]
    assert (column.non_blank, column.parsed) == (8, 7)
    assert column.failed_samples == ["abc"]
    assert rows(out, "SELECT table_name, column_name, raw_value FROM _quarantine") == [
        ("t", "v", "abc")
    ]


def test_money_and_integers_are_whole_numbers(tmp_path: Path, out: Path) -> None:
    raw, report = raw_upload(tmp_path, {"t.csv": "a,b\n1000.6,7\n2,2.5\n"})
    spec = DatasetSpec(
        tables=[
            TableSpec(
                source="t",
                name="t",
                columns=[
                    ColumnSpec(source="a", name="a", type="money"),
                    ColumnSpec(source="b", name="b", type="integer"),
                ],
            )
        ]
    )

    build(raw / RAW_DATABASE, spec, report, out)

    assert rows(out, "SELECT a, b FROM t ORDER BY _row") == [(1001, 7), (2, None)]


def test_quantities_keep_the_leading_number(tmp_path: Path, out: Path) -> None:
    raw, report = raw_upload(tmp_path, {"t.csv": "q\n1 hộp\n5 chai\n2\n"})

    build(
        raw / RAW_DATABASE,
        one_table(ColumnSpec(source="q", name="q", type="quantity")),
        report,
        out,
    )

    assert [r[0] for r in rows(out, "SELECT q FROM t ORDER BY _row")] == [1, 5, 2]


def test_dates_in_several_formats(tmp_path: Path, out: Path) -> None:
    text = "d\n01/02/2025\n45717\n1 thg 1 2025\n2025-01-03 06:09:58\n31/02/2025\n"
    raw, report = raw_upload(tmp_path, {"t.csv": text})
    column = ColumnSpec(
        source="d", name="d", type="date", date_formats=["dmy", "excel_serial", "vi_text"]
    )

    result = build(raw / RAW_DATABASE, one_table(column), report, out)

    assert [r[0] for r in rows(out, "SELECT d FROM t ORDER BY _row")] == [
        dt.date(2025, 2, 1),
        dt.date(2025, 3, 1),
        dt.date(2025, 1, 1),
        dt.date(2025, 1, 3),
        None,
    ]
    assert result.tables[0].columns[0].failed_samples == ["31/02/2025"]


def test_datetimes_keep_the_time(tmp_path: Path, out: Path) -> None:
    raw, report = raw_upload(tmp_path, {"t.csv": "d\n02/01/2025 06:09\n2025-01-03 07:00:31\n"})
    column = ColumnSpec(source="d", name="d", type="datetime", date_formats=["dmy"])

    build(raw / RAW_DATABASE, one_table(column), report, out)

    assert [r[0] for r in rows(out, "SELECT d FROM t ORDER BY _row")] == [
        dt.datetime(2025, 1, 2, 6, 9),
        dt.datetime(2025, 1, 3, 7, 0, 31),
    ]


def test_booleans(tmp_path: Path, out: Path) -> None:
    raw, report = raw_upload(tmp_path, {"t.csv": "b\ntrue\nCó\nkhông\nx\nmaybe\n"})

    build(
        raw / RAW_DATABASE, one_table(ColumnSpec(source="b", name="b", type="boolean")), report, out
    )

    assert [r[0] for r in rows(out, "SELECT b FROM t ORDER BY _row")] == [
        True,
        True,
        False,
        True,
        None,
    ]


def test_fill_down_stays_within_one_source(tmp_path: Path, out: Path) -> None:
    raw, report = raw_upload(tmp_path, {"a.csv": "k,v\nA,1\n,2\n", "b.csv": "k,v\n,3\nB,4\n"})
    spec = DatasetSpec(
        tables=[
            TableSpec(
                source="a",
                name="t",
                columns=[ColumnSpec(source="k", name="k"), ColumnSpec(source="v", name="v")],
                fill_down=["k"],
            )
        ]
    )

    build(raw / RAW_DATABASE, spec, report, out)

    assert rows(out, "SELECT k, v FROM t ORDER BY _source, _row") == [
        ("A", "1"),
        ("A", "2"),
        (None, "3"),
        ("B", "4"),
    ]


def test_exact_duplicates_are_removed_only_when_asked(tmp_path: Path, out: Path) -> None:
    raw, report = raw_upload(tmp_path, {"t.csv": "o,p\nD1,x\nD1,x\nD2,x\n"})
    column = ColumnSpec(source="o", name="o")

    result = build(raw / RAW_DATABASE, one_table(column, dedupe="exact"), report, out)

    assert rows(out, "SELECT o FROM t ORDER BY _row") == [("D1",), ("D2",)]
    assert (result.tables[0].raw_rows, result.tables[0].rows) == (3, 2)


def test_sensitive_columns_are_hidden_whatever_the_spec_says(tmp_path: Path, out: Path) -> None:
    raw, report = raw_upload(tmp_path, {"t.csv": "Tên,CCCD,Ghi chú\nAn,001099012345,riêng\n"})
    spec = DatasetSpec(
        tables=[
            TableSpec(
                source="t",
                name="t",
                columns=[
                    ColumnSpec(source="CCCD", name="cccd"),
                    ColumnSpec(source="Ghi chú", name="note", sensitive=True),
                ],
            )
        ]
    )

    result = build(raw / RAW_DATABASE, spec, report, out)

    with duckdb.connect(str(out / CLEAN_DATABASE), read_only=True) as connection:
        columns = [r[0] for r in connection.execute("DESCRIBE t").fetchall()]
    assert columns == ["ten", "_source", "_row"]
    assert sorted(result.tables[0].hidden) == ["CCCD", "Ghi chú"]
    assert "001099012345" not in result.model_dump_json()


def test_tables_and_columns_missing_from_the_spec_stay_as_text(tmp_path: Path, out: Path) -> None:
    raw, report = raw_upload(tmp_path, {"t.csv": "Số lượng\n3\n", "u.csv": "Mã hàng\nSP1\n"})
    spec = one_table(ColumnSpec(source="Số lượng", name="qty", type="integer"))

    build(raw / RAW_DATABASE, spec, report, out)

    assert rows(out, "SELECT qty FROM t") == [(3,)]
    assert rows(out, "SELECT ma_hang FROM u") == [("SP1",)]


def test_hostile_names_and_values_stay_data(tmp_path: Path, out: Path) -> None:
    hostile = 'x"); DROP TABLE t; --'
    raw, report = raw_upload(tmp_path, {"t.csv": f'"{hostile.replace(chr(34), chr(34) * 2)}"\n1\n'})
    column = ColumnSpec(
        source=hostile, name="v", type="boolean", true_values=["1'); DROP TABLE t; --"]
    )

    build(raw / RAW_DATABASE, one_table(column), report, out)

    assert rows(out, "SELECT count(*) FROM t") == [(1,)]


def test_grouped_numbers_never_become_decimals(tmp_path: Path, out: Path) -> None:
    values = ["1,350,000", "1.350.000", "1,234.56", "1.234,56", "22,000", "1.500", "0,5"]
    raw, report = raw_upload(tmp_path, {"t.csv": "v\n" + "\n".join(f'"{v}"' for v in values)})

    build(
        raw / RAW_DATABASE, one_table(ColumnSpec(source="v", name="v", type="number")), report, out
    )

    assert [r[0] for r in rows(out, "SELECT v FROM t ORDER BY _row")] == [
        1_350_000,
        1_350_000,
        1234.56,
        1234.56,
        22000,
        1500,
        0.5,
    ]


def test_vietnamese_shorthand_amounts(tmp_path: Path, out: Path) -> None:
    values = ["1tr270", "2tr9", "3tr045", "1k5", "15tr"]
    raw, report = raw_upload(tmp_path, {"t.csv": "v\n" + "\n".join(values)})

    build(
        raw / RAW_DATABASE, one_table(ColumnSpec(source="v", name="v", type="money")), report, out
    )

    assert [r[0] for r in rows(out, "SELECT v FROM t ORDER BY _row")] == [
        1_270_000,
        2_900_000,
        3_045_000,
        1500,
        15_000_000,
    ]


def test_day_month_dates_take_the_year_from_the_sheet_or_file(tmp_path: Path, out: Path) -> None:
    upload = tmp_path / "upload"
    upload.mkdir()
    import openpyxl

    book = openpyxl.Workbook()
    book.active.title = "T04_2025"
    book.active.append(["Ngày"])
    book.active.append(["28/4"])
    book.save(upload / "so.xlsx")
    (upload / "ban_2026.csv").write_text("Ngày\n2/1\n", encoding="utf-8")
    raw = tmp_path / "raw"
    raw.mkdir()
    report = ingest(upload, raw)
    column = ColumnSpec(source="Ngày", name="d", type="date", date_formats=["dm"])

    build(
        raw / RAW_DATABASE,
        DatasetSpec(tables=[TableSpec(source="ban_2026", name="t", columns=[column])]),
        report,
        out,
    )

    assert [r[0] for r in rows(out, "SELECT d FROM t ORDER BY _source")] == [
        dt.date(2026, 1, 2),
        dt.date(2025, 4, 28),
    ]


def test_unambiguous_date_forms_are_always_accepted(tmp_path: Path, out: Path) -> None:
    raw, report = raw_upload(tmp_path, {"t.csv": "d\n45717\n1 thg 1 2025\n01/02/2025\n"})
    column = ColumnSpec(source="d", name="d", type="date", date_formats=["dmy"])

    build(raw / RAW_DATABASE, one_table(column), report, out)

    assert [r[0] for r in rows(out, "SELECT d FROM t ORDER BY _row")] == [
        dt.date(2025, 3, 1),
        dt.date(2025, 1, 1),
        dt.date(2025, 2, 1),
    ]


def test_one_separator_before_a_suffix_is_a_decimal_point(tmp_path: Path, out: Path) -> None:
    values = ["151,036tr", "2,599tr", "1.5tr", "1.350.000k", "22,000", "2,5k"]
    raw, report = raw_upload(tmp_path, {"t.csv": "v\n" + "\n".join(f'"{v}"' for v in values)})

    build(
        raw / RAW_DATABASE, one_table(ColumnSpec(source="v", name="v", type="money")), report, out
    )

    assert [r[0] for r in rows(out, "SELECT v FROM t ORDER BY _row")] == [
        151_036_000,
        2_599_000,
        1_500_000,
        1_350_000_000,
        22_000,
        2_500,
    ]


SUBTOTALS = "Mã đơn,Sản phẩm,Tiền\nD1,Trà,10\n,Cà phê,20\n,,30\nD2,Trà,5\n,,5\n,TỔNG THÁNG 1,35\n"
SUBTOTAL_RULES = [
    RowRule(
        when=[
            RowCondition(column="Sản phẩm", op="blank"),
            RowCondition(column="Tiền", op="not_blank"),
        ],
        reason="daily subtotal",
    ),
    RowRule(
        when=[RowCondition(column="Sản phẩm", op="starts_with", value="tổng")],
        reason="monthly total",
    ),
]


def subtotal_spec(**table: object) -> DatasetSpec:
    columns = [
        ColumnSpec(source="Mã đơn", name="order_id"),
        ColumnSpec(source="Sản phẩm", name="product"),
        ColumnSpec(source="Tiền", name="amount", type="money"),
    ]
    return DatasetSpec(tables=[TableSpec(source="t", name="t", columns=columns, **table)])


def test_excluded_rows_leave_the_table_before_fill_down(tmp_path: Path, out: Path) -> None:
    raw, report = raw_upload(tmp_path, {"t.csv": SUBTOTALS})
    spec = subtotal_spec(fill_down=["Mã đơn"], exclude_rows=SUBTOTAL_RULES)

    result = build(raw / RAW_DATABASE, spec, report, out)

    assert rows(out, "SELECT order_id, product, amount FROM t ORDER BY _row") == [
        ("D1", "Trà", 10),
        ("D1", "Cà phê", 20),
        ("D2", "Trà", 5),
    ]
    table = result.tables[0]
    assert (table.raw_rows, table.rows) == (6, 3)
    assert [(e.reason, e.rows) for e in table.excluded] == [
        ("daily subtotal", 2),
        ("monthly total", 1),
    ]


def test_excluded_rows_are_kept_with_their_reason_and_raw_values(tmp_path: Path, out: Path) -> None:
    raw, report = raw_upload(tmp_path, {"t.csv": SUBTOTALS})

    build(raw / RAW_DATABASE, subtotal_spec(exclude_rows=SUBTOTAL_RULES), report, out)

    excluded = rows(out, "SELECT table_name, reason, _source, _row, raw_values FROM _excluded")
    assert [(t, r, s, n) for t, r, s, n, _ in excluded] == [
        ("t", "daily subtotal", "t.csv", 4),
        ("t", "daily subtotal", "t.csv", 6),
        ("t", "monthly total", "t.csv", 7),
    ]
    assert json.loads(excluded[2][4]) == {"Mã đơn": None, "Sản phẩm": "TỔNG THÁNG 1", "Tiền": "35"}


def test_excluded_rows_do_not_count_against_parse_rates(tmp_path: Path, out: Path) -> None:
    raw, report = raw_upload(tmp_path, {"t.csv": "Ngày,Tiền\n01/02/2025,10\nTổng ngày,10\n"})
    spec = DatasetSpec(
        tables=[
            TableSpec(
                source="t",
                name="t",
                columns=[ColumnSpec(source="Ngày", name="day", type="date", date_formats=["dmy"])],
                exclude_rows=[
                    RowRule(when=[RowCondition(column="Ngày", op="contains", value="TỔNG")])
                ],
            )
        ]
    )

    result = build(raw / RAW_DATABASE, spec, report, out)

    assert result.tables[0].columns[0].parse_rate == 1.0
    assert rows(out, "SELECT count(*) FROM _quarantine") == [(0,)]


def test_excluded_raw_values_never_hold_sensitive_columns(tmp_path: Path, out: Path) -> None:
    raw, report = raw_upload(tmp_path, {"t.csv": "Tên,CCCD\nAn,001099012345\nTổng,001099012346\n"})
    rule = RowRule(when=[RowCondition(column="Tên", op="equals", value="tổng")], reason="total")
    spec = DatasetSpec(tables=[TableSpec(source="t", name="t", exclude_rows=[rule])])

    build(raw / RAW_DATABASE, spec, report, out)

    assert rows(out, "SELECT raw_values FROM _excluded") == [('{"Tên":"Tổng"}',)]


def test_exclusion_values_stay_data(tmp_path: Path, out: Path) -> None:
    raw, report = raw_upload(tmp_path, {"t.csv": "a\nx\n\"it's'); DROP TABLE t; --\"\n"})
    hostile = "it's'); DROP TABLE t; --"
    rule = RowRule(when=[RowCondition(column="a", op="equals", value=hostile)])
    spec = DatasetSpec(tables=[TableSpec(source="t", name="t", exclude_rows=[rule])])

    result = build(raw / RAW_DATABASE, spec, report, out)

    assert rows(out, "SELECT a FROM t") == [("x",)]
    assert result.tables[0].excluded[0].rows == 1


def test_rules_sharing_a_reason_are_counted_apart(tmp_path: Path, out: Path) -> None:
    raw, report = raw_upload(tmp_path, {"t.csv": "a\nx\ny\ny\n"})
    rules = [
        RowRule(when=[RowCondition(column="a", op="equals", value=v)], reason="note")
        for v in ("x", "y")
    ]
    spec = DatasetSpec(tables=[TableSpec(source="t", name="t", exclude_rows=rules)])

    result = build(raw / RAW_DATABASE, spec, report, out)

    assert [(e.reason, e.rows) for e in result.tables[0].excluded] == [
        ("note", 1),
        ("note (2)", 2),
    ]


def daily_sales_upload(tmp_path: Path) -> tuple[Path, object]:
    lines = ["Ngày,Sản phẩm,Tiền,Ghi chú"]
    lines += ["01/03/2025,Trà,1000,"] * 40 + ["01/03/2025,Trà,1000,gấp"] * 10
    lines += [",Tổng ngày 01/03,50000,"]
    lines += ["02/03/2025,Cà phê,2500,"] * 30
    lines += [",Tổng ngày 02/03,75000,", ",Tổng tháng 3,125000,"]
    return raw_upload(tmp_path, {"t.csv": "\n".join(lines) + "\n"})


def test_kept_rows_that_sum_the_rows_above_are_reported(tmp_path: Path, out: Path) -> None:
    raw, report = daily_sales_upload(tmp_path)
    spec = DatasetSpec(tables=[TableSpec(source="t", name="t")])

    result = build(raw / RAW_DATABASE, spec, report, out)

    [check] = result.tables[0].subtotal_checks
    assert (check.columns, check.rows, check.sums_above, check.kept) == (
        ["Sản phẩm", "Tiền"],
        3,
        2,
        3,
    )


def test_excluded_subtotals_are_not_reported_as_kept(tmp_path: Path, out: Path) -> None:
    raw, report = daily_sales_upload(tmp_path)
    rule = RowRule(when=[RowCondition(column="Sản phẩm", op="starts_with", value="tổng")])
    spec = DatasetSpec(tables=[TableSpec(source="t", name="t", exclude_rows=[rule])])

    result = build(raw / RAW_DATABASE, spec, report, out)

    assert result.tables[0].subtotal_checks[0].kept == 0


def test_rules_that_remove_rows_filled_like_records_are_counted(tmp_path: Path, out: Path) -> None:
    raw, report = daily_sales_upload(tmp_path)
    rules = [
        RowRule(when=[RowCondition(column="Ghi chú", op="not_blank")], reason="notes"),
        RowRule(when=[RowCondition(column="Ngày", op="blank")], reason="subtotals"),
    ]
    spec = DatasetSpec(tables=[TableSpec(source="t", name="t", exclude_rows=rules)])

    result = build(raw / RAW_DATABASE, spec, report, out)

    assert [(e.reason, e.rows, e.full_rows) for e in result.tables[0].excluded] == [
        ("notes", 10, 10),
        ("subtotals", 3, 0),
    ]


def test_rules_that_remove_rows_with_an_identifier_are_counted(tmp_path: Path, out: Path) -> None:
    lines = ["Mã đơn,Khách hàng,Thành tiền"]
    lines += [f"HD{i:04d},{'' if i % 25 == 0 else 'An'},{1000 + i % 7}" for i in range(100)]
    raw, report = raw_upload(tmp_path, {"t.csv": "\n".join(lines) + "\n"})
    rule = RowRule(when=[RowCondition(column="Khách hàng", op="blank")], reason="no customer")
    spec = DatasetSpec(tables=[TableSpec(source="t", name="t", exclude_rows=[rule])])

    result = build(raw / RAW_DATABASE, spec, report, out)

    [excluded] = result.tables[0].excluded
    assert (excluded.rows, excluded.with_identifier, excluded.identifier) == (4, 4, "Mã đơn")


def test_amounts_and_repeated_keys_are_not_identifiers(tmp_path: Path, out: Path) -> None:
    raw, report = daily_sales_upload(tmp_path)
    rule = RowRule(when=[RowCondition(column="Sản phẩm", op="starts_with", value="tổng")])
    spec = DatasetSpec(tables=[TableSpec(source="t", name="t", exclude_rows=[rule])])

    result = build(raw / RAW_DATABASE, spec, report, out)

    assert result.tables[0].excluded[0].with_identifier == 0


def test_notes_written_in_an_identifier_column_are_not_identifiers(
    tmp_path: Path, out: Path
) -> None:
    lines = ["Mã phiếu,Mặt hàng,Nhập"]
    lines += [f"PK{i:04d},Trà,{i % 9 + 1}" for i in range(60)]
    lines.insert(30, "Lưu ý: hàng đổi trả ghi số âm,,")
    raw, report = raw_upload(tmp_path, {"t.csv": "\n".join(lines) + "\n"})
    rule = RowRule(when=[RowCondition(column="Mặt hàng", op="blank")], reason="note")
    spec = DatasetSpec(tables=[TableSpec(source="t", name="t", exclude_rows=[rule])])

    result = build(raw / RAW_DATABASE, spec, report, out)

    assert (result.tables[0].excluded[0].rows, result.tables[0].excluded[0].with_identifier) == (
        1,
        0,
    )


def test_text_columns_with_few_values_list_them_by_frequency(tmp_path: Path, out: Path) -> None:
    """Values are listed as stored, so a query that copies one matches it."""
    statuses = ["completed"] * 5 + ["cancelled"] * 2 + ["returned"] + [""]
    raw, report = raw_upload(tmp_path, {"t.csv": "s\n" + "\n".join(statuses)})

    result = build(raw / RAW_DATABASE, one_table(ColumnSpec(source="s", name="s")), report, out)

    assert result.tables[0].columns[0].values == ["completed", "cancelled", "returned"]


def test_text_columns_with_many_values_list_none(tmp_path: Path, out: Path) -> None:
    raw, report = raw_upload(tmp_path, {"t.csv": "s\n" + "\n".join(f"v{i}" for i in range(40))})

    result = build(raw / RAW_DATABASE, one_table(ColumnSpec(source="s", name="s")), report, out)

    assert result.tables[0].columns[0].values == []


def test_long_text_values_are_not_listed(tmp_path: Path, out: Path) -> None:
    raw, report = raw_upload(tmp_path, {"t.csv": "s\n" + "\n".join(["x" * 300, "y" * 300])})

    result = build(raw / RAW_DATABASE, one_table(ColumnSpec(source="s", name="s")), report, out)

    assert result.tables[0].columns[0].values == []


def test_typed_columns_list_no_values(tmp_path: Path, out: Path) -> None:
    raw, report = raw_upload(tmp_path, {"t.csv": "v\n1\n2\n"})

    result = build(
        raw / RAW_DATABASE, one_table(ColumnSpec(source="v", name="v", type="integer")), report, out
    )

    assert result.tables[0].columns[0].values == []


def test_unlisted_columns_keep_identifier_names(tmp_path: Path, out: Path) -> None:
    raw, report = raw_upload(tmp_path, {"t.csv": "TriGia,Tổng tiền\n1,2\n"})

    result = build(raw / RAW_DATABASE, DatasetSpec(tables=[]), report, out)

    assert [c.name for c in result.tables[0].columns] == ["TriGia", "tong_tien"]
    assert rows(out, "SELECT trigia, tong_tien FROM t") == [("1", "2")]


def test_repeated_rows_are_counted_with_the_key_that_proves_them(tmp_path: Path, out: Path) -> None:
    """Order and line number identify every distinct row, so a repeat is a double entry."""
    lines = "o,n,item\no1,1,a\no1,1,a\no1,2,b\no2,1,a\n"
    raw, report = raw_upload(tmp_path, {"t.csv": lines})

    result = build(raw / RAW_DATABASE, DatasetSpec(tables=[]), report, out)

    [table] = result.tables
    assert (table.duplicate_rows, table.duplicate_key) == (1, ["o", "n"])


def test_repeated_rows_without_a_key_are_counted_but_not_proven(tmp_path: Path, out: Path) -> None:
    """A sales log with no ids can hold two identical sales, so no column set proves a repeat."""
    lines = "day,item,qty\n1,x,2\n1,x,2\n1,x,3\n1,y,2\n2,x,2\n"
    raw, report = raw_upload(tmp_path, {"t.csv": lines})

    result = build(raw / RAW_DATABASE, DatasetSpec(tables=[]), report, out)

    [table] = result.tables
    assert (table.duplicate_rows, table.duplicate_key) == (1, [])


def test_deduplicated_tables_report_no_repeats(tmp_path: Path, out: Path) -> None:
    raw, report = raw_upload(tmp_path, {"t.csv": "o,n,item\no1,1,a\no1,1,a\no1,2,b\n"})
    spec = DatasetSpec(tables=[TableSpec(source="t", name="t", dedupe="exact")])

    result = build(raw / RAW_DATABASE, spec, report, out)

    assert result.tables[0].duplicate_rows == 0
    assert rows(out, "SELECT count(*) FROM t") == [(2,)]


def test_the_spec_description_of_a_row_reaches_the_report(tmp_path: Path, out: Path) -> None:
    raw, report = raw_upload(tmp_path, {"t.csv": "o,item\no1,a\n"})
    spec = DatasetSpec(tables=[TableSpec(source="t", name="t", description="one order line")])

    result = build(raw / RAW_DATABASE, spec, report, out)

    assert result.tables[0].description == "one order line"


def test_date_columns_report_the_range_they_cover(tmp_path: Path, out: Path) -> None:
    raw, report = raw_upload(
        tmp_path,
        {
            "t.csv": "d,at,s\n2025-03-02,2025-03-02 10:00:00,x\n"
            "2025-01-05,2026-06-28 21:15:00,y\n,,z\n"
        },
    )
    spec = DatasetSpec(
        tables=[
            TableSpec(
                source="t",
                name="t",
                columns=[
                    ColumnSpec(source="d", name="d", type="date"),
                    ColumnSpec(source="at", name="at", type="datetime"),
                    ColumnSpec(source="s", name="s"),
                ],
            )
        ]
    )

    result = build(raw / RAW_DATABASE, spec, report, out)

    d, at, s = result.tables[0].columns
    assert (d.min_value, d.max_value) == ("2025-01-05", "2025-03-02")
    assert (at.min_value, at.max_value) == ("2025-03-02 10:00:00", "2026-06-28 21:15:00")
    assert (s.min_value, s.max_value) == (None, None)
