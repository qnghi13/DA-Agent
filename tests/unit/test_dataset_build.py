"""Building clean tables from raw tables and a DatasetSpec, with parse rates and quarantine."""

import datetime as dt
from pathlib import Path

import duckdb
import pytest

from da_agent.ingestion.build import CLEAN_DATABASE, build
from da_agent.ingestion.raw import RAW_DATABASE, ingest
from da_agent.ingestion.spec import ColumnSpec, DatasetSpec, TableSpec


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
