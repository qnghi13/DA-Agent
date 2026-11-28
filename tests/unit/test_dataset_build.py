"""Building clean tables from raw tables and a DatasetSpec, with parse rates and quarantine."""

import datetime as dt
import json
from pathlib import Path

import duckdb
import pytest

from da_agent.ingestion.build import CLEAN_DATABASE, BuildReport, build
from da_agent.ingestion.raw import RAW_DATABASE, ingest
from da_agent.ingestion.spec import (
    ColumnSpec,
    DatasetSpec,
    OverlapDrop,
    RowCondition,
    RowRule,
    TableSpec,
)


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


def test_negative_shorthand_amounts(tmp_path: Path, out: Path) -> None:
    """Refunds and returns are written with a minus sign, as in -2tr250."""
    values = ["-2tr250", "-1tr04", "-1k5", "-15tr"]
    raw, report = raw_upload(tmp_path, {"t.csv": "v\n" + "\n".join(values)})

    build(
        raw / RAW_DATABASE, one_table(ColumnSpec(source="v", name="v", type="money")), report, out
    )

    assert [r[0] for r in rows(out, "SELECT v FROM t ORDER BY _row")] == [
        -2_250_000,
        -1_040_000,
        -1500,
        -15_000_000,
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

    # The daily totals are proven first, then the monthly total over the whole month.
    [check] = result.tables[0].subtotal_checks
    assert (check.columns, check.rows, check.sums_above, check.kept) == (
        ["Sản phẩm", "Tiền"],
        3,
        2,
        0,
    )


def test_rows_proven_to_sum_the_rows_above_leave_the_table(tmp_path: Path, out: Path) -> None:
    """A narrow model rule can miss subtotal rows; code removes the ones it can prove (ADR 0029)."""
    raw, report = daily_sales_upload(tmp_path)
    spec = DatasetSpec(tables=[TableSpec(source="t", name="t")])

    result = build(raw / RAW_DATABASE, spec, report, out)

    assert result.tables[0].proven_subtotals == 3
    assert rows(out, "SELECT sum(CAST(tien AS INTEGER)) FROM t") == [(125_000,)]
    excluded = rows(out, "SELECT _row FROM _excluded ORDER BY _row")
    assert excluded == [(52,), (83,), (84,)]


def test_a_total_over_subtotals_a_rule_removed_is_proven(tmp_path: Path, out: Path) -> None:
    """With the daily totals gone, the monthly total equals the sum of the month's records."""
    raw, report = daily_sales_upload(tmp_path)
    rule = RowRule(when=[RowCondition(column="Sản phẩm", op="starts_with", value="tổng ngày")])
    spec = DatasetSpec(tables=[TableSpec(source="t", name="t", exclude_rows=[rule])])

    result = build(raw / RAW_DATABASE, spec, report, out)

    assert result.tables[0].proven_subtotals == 1
    assert rows(out, "SELECT sum(CAST(tien AS INTEGER)) FROM t") == [(125_000,)]


def test_a_row_equal_to_one_record_above_is_not_proven(tmp_path: Path, out: Path) -> None:
    """One record and a row with the same amount may both be sales, so the proof needs two."""
    block = ["D,Trà,"] * 25 + ["D,Trà,1000", ",,1000"]
    upload = "Mã,Sản phẩm,Tiền\n" + "\n".join(block * 3) + "\n"
    raw, report = raw_upload(tmp_path, {"t.csv": upload})
    spec = DatasetSpec(tables=[TableSpec(source="t", name="t")])

    result = build(raw / RAW_DATABASE, spec, report, out)

    assert result.tables[0].proven_subtotals == 0
    assert rows(out, "SELECT count(*) FROM _excluded") == [(0,)]


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


def test_subtotals_a_narrow_rule_missed_are_proven_by_their_own_day(
    tmp_path: Path, out: Path
) -> None:
    """A rule that removes some daily totals must not stretch the other days' groups."""
    lines = ["Mã,Sản phẩm,Tiền,Nợ"]
    for day in range(1, 7):
        lines += [f"D{day}{n},Trà,1000,0" for n in range(30)]
        debt = "0" if day % 2 else "500"
        lines += [f"tổng {day:02d}/05,,30000,{debt}"]
    lines += ["Cộng tháng 05,,180000,1500"]
    raw, report = raw_upload(tmp_path, {"t.csv": "\n".join(lines) + "\n"})
    narrow = RowRule(
        when=[
            RowCondition(column="Mã", op="starts_with", value="tổng"),
            RowCondition(column="Nợ", op="equals", value="0"),
        ]
    )
    spec = DatasetSpec(tables=[TableSpec(source="t", name="t", exclude_rows=[narrow])])

    result = build(raw / RAW_DATABASE, spec, report, out)

    assert result.tables[0].proven_subtotals == 4
    assert rows(out, "SELECT count(*), sum(CAST(tien AS INTEGER)) FROM t") == [(180, 180_000)]


# Column profile (docs/design/2026-11-14-data-overview.md)


def test_number_columns_get_distinct_range_and_mean(tmp_path: Path, out: Path) -> None:
    raw, report = raw_upload(tmp_path, {"t.csv": "v\n10\n20\n20\n\nabc\n60\n"})

    result = build(
        raw / RAW_DATABASE, one_table(ColumnSpec(source="v", name="v", type="number")), report, out
    )

    column = result.tables[0].columns[0]
    assert column.distinct == 3
    assert (column.number_min, column.number_max, column.number_mean) == (10, 60, 27.5)
    assert column.top_values == []
    assert (column.min_value, column.max_value) == (None, None)


def test_text_columns_get_their_most_frequent_values_with_counts(tmp_path: Path, out: Path) -> None:
    values = ["completed"] * 4 + ["Completed"] * 2 + ["cancelled", "a", "b", "c", "x" * 80, ""]
    raw, report = raw_upload(tmp_path, {"t.csv": "s\n" + "\n".join(values)})

    result = build(raw / RAW_DATABASE, one_table(ColumnSpec(source="s", name="s")), report, out)

    column = result.tables[0].columns[0]
    assert column.distinct == 7
    assert [(v.value, v.rows) for v in column.top_values] == [
        ("completed", 4),
        ("Completed", 2),
        ("a", 1),
        ("b", 1),
        ("c", 1),
    ]
    assert column.number_min is None and column.number_mean is None


def test_long_text_values_are_cut_in_the_profile(tmp_path: Path, out: Path) -> None:
    raw, report = raw_upload(tmp_path, {"t.csv": "s\n" + "\n".join(["y" * 80] * 2 + ["z"])})

    result = build(raw / RAW_DATABASE, one_table(ColumnSpec(source="s", name="s")), report, out)

    assert [(v.value, v.rows) for v in result.tables[0].columns[0].top_values] == [
        ("y" * 60, 2),
        ("z", 1),
    ]


def test_date_columns_keep_their_range_and_get_distinct(tmp_path: Path, out: Path) -> None:
    raw, report = raw_upload(tmp_path, {"t.csv": "d\n2026-06-01\n2026-06-01\n2026-06-30\n"})
    spec = one_table(ColumnSpec(source="d", name="d", type="date"))

    column = build(raw / RAW_DATABASE, spec, report, out).tables[0].columns[0]

    assert column.distinct == 2
    assert (column.min_value, column.max_value) == ("2026-06-01", "2026-06-30")
    assert column.number_min is None and column.top_values == []


# --- Overlap between new files and older rows (docs/design/2026-11-21-add-files.md) ---


def orders_spec(**table: object) -> DatasetSpec:
    return DatasetSpec(tables=[TableSpec(source="orders", name="orders", **table)])


def overlap_build(
    tmp_path: Path, out: Path, files: dict[str, str], spec=None, new=("orders_1.csv",)
):
    raw, report = raw_upload(tmp_path, files)
    return build(
        raw / RAW_DATABASE,
        spec or orders_spec(),
        report,
        out,
        new_sources=list(new),
    )


def test_overlap_exact_counts_resent_rows(tmp_path: Path, out: Path) -> None:
    rows_text = "order_id,status\n1,paid\n2,paid\n3,open\n"

    result = overlap_build(tmp_path, out, {"orders.csv": rows_text, "orders_1.csv": rows_text})

    table = result.tables[0]
    assert table.new_rows == 3
    exact = [o for o in table.overlaps if o.kind == "exact"]
    assert len(exact) == 1 and exact[0].rows == 3
    assert len(exact[0].examples) == 3
    assert [o.kind for o in table.overlaps] == ["exact"]


def test_overlap_key_numeric_order_id(tmp_path: Path, out: Path) -> None:
    old = "order_id,status\n1,open\n2,open\n3,open\n4,open\n"
    new = "order_id,status\n2,paid\n3,paid\n9,open\n"

    result = overlap_build(tmp_path, out, {"orders.csv": old, "orders_1.csv": new})

    table = result.tables[0]
    assert [(o.kind, o.rows, o.key) for o in table.overlaps] == [("key", 2, ["order_id"])]
    assert table.overlaps[0].examples[0]["order_id"] in ("2", "3")


def test_overlap_key_two_columns_for_line_items(tmp_path: Path, out: Path) -> None:
    old = "order_id,product,qty\n1,a,1\n1,b,1\n2,a,1\n2,b,1\n"
    new = "order_id,product,qty\n1,a,5\n2,c,1\n"

    result = overlap_build(tmp_path, out, {"orders.csv": old, "orders_1.csv": new})

    assert [(o.kind, o.rows, o.key) for o in result.tables[0].overlaps] == [
        ("key", 1, ["order_id", "product"])
    ]


def test_overlap_date_range(tmp_path: Path, out: Path) -> None:
    old = "order_id,day\n1,2025-01-01\n2,2025-01-31\n"
    inside = "order_id,day\n7,2025-01-15\n8,2025-02-03\n"
    after = "order_id,day\n7,2025-02-02\n8,2025-02-03\n"
    spec = orders_spec(columns=[ColumnSpec(source="day", name="day", type="date")])

    for name in ("a", "b", "clean_b"):
        (tmp_path / name).mkdir()
    hit = overlap_build(tmp_path / "a", out, {"orders.csv": old, "orders_1.csv": inside}, spec)
    miss = overlap_build(
        tmp_path / "b", tmp_path / "clean_b", {"orders.csv": old, "orders_1.csv": after}, spec
    )

    dates = [o for o in hit.tables[0].overlaps if o.kind == "date"]
    assert [(o.column, o.rows) for o in dates] == [("day", 1)]
    assert dates[0].examples == [{"day": "2025-01-15"}]
    assert [o for o in miss.tables[0].overlaps if o.kind == "date"] == []


@pytest.mark.parametrize(("order", "later", "earlier"), [
    (("orders_dec_b.csv", "orders_dec_a.csv"), "orders_dec_a.csv", "orders_dec_b.csv"),
    (("orders_dec_a.csv", "orders_dec_b.csv"), "orders_dec_b.csv", "orders_dec_a.csv"),
])  # fmt: skip
def test_new_files_of_one_upload_are_checked_against_each_other_in_upload_order(
    tmp_path: Path, out: Path, order: tuple[str, str], later: str, earlier: str
) -> None:
    old = "order_id,status\n1,paid\n2,paid\n"
    december = "order_id,status\n7,paid\n8,open\n"
    files = {"orders.csv": old, "orders_dec_a.csv": december, "orders_dec_b.csv": december}

    result = overlap_build(tmp_path, out, files, new=order)

    exact = [o for o in result.tables[0].overlaps if o.kind == "exact"]
    assert [(o.rows, o.earlier_new_files) for o in exact] == [(2, [earlier])]
    # Only the later file's rows are counted: the earlier one is compared with old rows only.
    assert {e["order_id"] for e in exact[0].examples} == {"7", "8"}
    assert len(exact[0].examples) == 2
    assert result.tables[0].new_rows == 4
    assert later not in exact[0].earlier_new_files


def test_a_key_repeated_between_new_files_is_a_key_overlap(tmp_path: Path, out: Path) -> None:
    old = "order_id,status\n1,open\n2,open\n3,open\n"
    first = "order_id,status\n7,open\n"
    second = "order_id,status\n7,paid\n9,open\n"
    files = {"orders.csv": old, "orders_dec_1.csv": first, "orders_dec_2.csv": second}

    result = overlap_build(tmp_path, out, files, new=("orders_dec_1.csv", "orders_dec_2.csv"))

    assert [(o.kind, o.rows, o.key, o.earlier_new_files) for o in result.tables[0].overlaps] == [
        ("key", 1, ["order_id"], ["orders_dec_1.csv"])
    ]


def test_an_overlap_with_old_rows_only_names_no_new_file(tmp_path: Path, out: Path) -> None:
    text = "order_id,status\n1,paid\n"

    result = overlap_build(tmp_path, out, {"orders.csv": text, "orders_1.csv": text})

    assert [o.earlier_new_files for o in result.tables[0].overlaps] == [[]]


def test_date_overlaps_compare_each_earlier_new_file_by_its_own_dates(
    tmp_path: Path, out: Path
) -> None:
    old = "order_id,day\n1,2025-01-01\n2,2025-01-31\n"
    december = "order_id,day\n5,2025-12-01\n6,2025-12-31\n"
    november = "order_id,day\n7,2025-11-10\n"
    late_december = "order_id,day\n8,2025-12-15\n"
    spec = orders_spec(columns=[ColumnSpec(source="day", name="day", type="date")])
    new = {
        "orders_dec.csv": december,
        "orders_nov.csv": november,
        "orders_dec_2.csv": late_december,
    }

    result = overlap_build(tmp_path, out, {"orders.csv": old, **new}, spec, new=tuple(new))

    # November falls between January and December but inside neither file's own dates.
    dates = [o for o in result.tables[0].overlaps if o.kind == "date"]
    assert [(o.rows, o.earlier_new_files, o.examples) for o in dates] == [
        (1, ["orders_dec.csv"], [{"day": "2025-12-15"}])
    ]


def test_drop_overlap_against_an_earlier_new_file(tmp_path: Path, out: Path) -> None:
    old = "order_id,status\n1,paid\n"
    december = "order_id,status\n7,paid\n"
    spec = orders_spec(
        drop_overlap=[
            OverlapDrop(file="orders_dec_b.csv", against=["orders.csv"], key=[]),
            OverlapDrop(file="orders_dec_a.csv", against=["orders.csv", "orders_dec_b.csv"]),
        ]
    )
    files = {"orders.csv": old, "orders_dec_a.csv": december, "orders_dec_b.csv": december}

    overlap_build(tmp_path, out, files, spec, new=("orders_dec_b.csv", "orders_dec_a.csv"))

    assert rows(out, "SELECT order_id, _source FROM orders ORDER BY _source") == [
        ("1", "orders.csv"),
        ("7", "orders_dec_b.csv"),
    ]
    assert rows(out, "SELECT reason, _source FROM _excluded") == [
        ("already in orders.csv, orders_dec_b.csv", "orders_dec_a.csv")
    ]


def test_no_overlap_without_new_sources(tmp_path: Path, out: Path) -> None:
    text = "order_id,status\n1,paid\n"
    raw, report = raw_upload(tmp_path, {"orders.csv": text, "orders_1.csv": text})

    result = build(raw / RAW_DATABASE, orders_spec(), report, out)

    table = result.tables[0]
    assert (table.overlaps, table.new_rows, table.new_columns, table.new_excluded) == (
        [],
        0,
        [],
        [],
    )


def test_overlap_ignores_dedupe_flag(tmp_path: Path, out: Path) -> None:
    text = "order_id,status\n1,paid\n2,paid\n"

    result = overlap_build(
        tmp_path, out, {"orders.csv": text, "orders_1.csv": text}, orders_spec(dedupe="exact")
    )

    assert [(o.kind, o.rows) for o in result.tables[0].overlaps] == [("exact", 2)]
    assert rows(out, "SELECT count(*) FROM orders") == [(2,)]


def test_new_columns_and_new_excluded_count_only_new_rows(tmp_path: Path, out: Path) -> None:
    old = "order_id,amount\n1,10\nTotal,10\n"
    new = "order_id,amount\n5,abc\n6,20\nTotal,20\n"
    spec = orders_spec(
        columns=[ColumnSpec(source="amount", name="amount", type="number")],
        exclude_rows=[
            RowRule(
                when=[RowCondition(column="order_id", op="equals", value="total")], reason="total"
            )
        ],
    )

    result = overlap_build(tmp_path, out, {"orders.csv": old, "orders_1.csv": new}, spec)

    table = result.tables[0]
    assert table.new_rows == 2
    amount = next(c for c in table.new_columns if c.name == "amount")
    assert (amount.non_blank, amount.parsed, amount.failed_samples) == (2, 1, ["abc"])
    assert [(r.reason, r.rows) for r in table.excluded] == [("total", 2)]
    assert [(r.reason, r.rows, r.position) for r in table.new_excluded] == [("total", 1, 1)]
    # Quarantine is written once, from the whole table.
    assert rows(out, "SELECT count(*) FROM _quarantine") == [(1,)]


def test_drop_overlap_moves_rows_to_excluded(tmp_path: Path, out: Path) -> None:
    old = "order_id,status\n1,paid\n2,paid\n"
    new = "order_id,status\n1,paid\n3,paid\n"
    spec = orders_spec(
        drop_overlap=[OverlapDrop(file="orders_1.csv", against=["orders.csv"], key=[])]
    )

    result = overlap_build(tmp_path, out, {"orders.csv": old, "orders_1.csv": new}, spec)

    assert rows(out, "SELECT order_id, _source FROM orders ORDER BY _source, _row") == [
        ("1", "orders.csv"),
        ("2", "orders.csv"),
        ("3", "orders_1.csv"),
    ]
    assert rows(out, "SELECT reason, _source, raw_values FROM _excluded") == [
        ("already in orders.csv", "orders_1.csv", '{"order_id":"1","status":"paid"}')
    ]
    assert result.tables[0].new_rows == 1


def test_new_overlap_dropped_counts_dropped_rows_and_not_rule_rows(
    tmp_path: Path, out: Path
) -> None:
    rows_1 = "".join(f"{i},10\n" for i in range(1, 7))
    old = f"order_id,amount\n{rows_1}Total,60\n"
    spec = orders_spec(
        exclude_rows=[
            RowRule(
                when=[RowCondition(column="order_id", op="equals", value="total")], reason="total"
            )
        ],
        drop_overlap=[OverlapDrop(file="orders_1.csv", against=["orders.csv"], key=[])],
    )

    result = overlap_build(tmp_path, out, {"orders.csv": old, "orders_1.csv": old}, spec)

    table = result.tables[0]
    assert (table.new_rows, table.new_overlap_dropped) == (0, 6)
    assert [(r.reason, r.rows) for r in table.new_excluded] == [("total", 1)]
    assert BuildReport.model_validate_json(result.model_dump_json()) == result


def test_drop_overlap_by_key_keeps_changed_rows_out(tmp_path: Path, out: Path) -> None:
    old = "order_id,status\n1,open\n2,open\n"
    new = "order_id,status\n1,paid\n3,open\n"
    spec = orders_spec(
        drop_overlap=[OverlapDrop(file="orders_1.csv", against=["orders.csv"], key=["order_id"])]
    )

    overlap_build(tmp_path, out, {"orders.csv": old, "orders_1.csv": new}, spec)

    assert rows(out, "SELECT order_id FROM orders WHERE _source = 'orders_1.csv'") == [("3",)]


def test_drop_overlap_quotes_names(tmp_path: Path, out: Path) -> None:
    key = "k'\"x"
    header = '"k\'""x",v\n'
    file_old, file_new = "o'ld\".csv", "o'ld\"_1.csv"
    raw, report = raw_upload(
        tmp_path, {file_old: header + "1,a\n2,a\n", file_new: header + "1,b\n3,b\n"}
    )
    drop = OverlapDrop(file=file_new, against=[file_old], key=[key])
    spec = DatasetSpec(
        tables=[TableSpec(source=report.tables[0].name, name="t", drop_overlap=[drop])]
    )

    result = build(raw / RAW_DATABASE, spec, report, out, new_sources=[file_new])

    assert result.tables[0].error is None
    assert rows(out, "SELECT reason FROM _excluded") == [(f"already in {file_old}",)]
    assert rows(out, "SELECT count(*) FROM t") == [(3,)]


def test_a_layout_table_keeps_its_cell_ranges_through_build(tmp_path: Path, out: Path) -> None:
    import openpyxl

    from da_agent.ingestion.layout_spec import LayoutSpec

    upload, raw = tmp_path / "upload", tmp_path / "raw"
    upload.mkdir()
    raw.mkdir()
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "S"
    for row in [
        ["SALES"],
        ["Order", "Item", "Amount"],
        ["D1", "tea", "10"],
        [None, "coffee", "20"],
        [None, "note: call back", None],
        ["D2", "tea", "5"],
    ]:
        sheet.append(row)
    book.save(upload / "t.xlsx")
    [finding] = ingest(upload, tmp_path / "probe").layout
    layout = LayoutSpec.model_validate({"families": [{"family": finding.family}]})
    report = ingest(upload, raw, layout=layout)
    assert [t.name for t in report.tables] == ["t"], report.layout
    spec = DatasetSpec(
        tables=[
            TableSpec(
                source="t",
                name="t",
                columns=[
                    ColumnSpec(source="Order", name="order_id"),
                    ColumnSpec(source="Item", name="item"),
                    ColumnSpec(source="Amount", name="amount", type="integer"),
                ],
                fill_down=["Order"],
                exclude_rows=[
                    RowRule(when=[RowCondition(column="Amount", op="blank")], reason="note")
                ],
            )
        ]
    )

    build(raw / RAW_DATABASE, spec, report, out)

    assert rows(out, "SELECT order_id, item, _row, _cell FROM t ORDER BY _row") == [
        ("D1", "tea", 1, "A3:C3"),
        ("D1", "coffee", 2, "A4:C4"),
        ("D2", "tea", 4, "A6:C6"),
    ]
    assert rows(out, "SELECT reason, _row, _cell FROM _excluded") == [("note", 3, "A5:C5")]
