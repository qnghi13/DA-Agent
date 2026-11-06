"""Raw ingestion: safety checks, readers, header detection, profiles, and the raw DuckDB file."""

import datetime as dt
import sqlite3
import zipfile
from pathlib import Path

import duckdb
import openpyxl
import pytest

from da_agent.ingestion.limits import IngestLimits
from da_agent.ingestion.raw import ingest


def write_xlsx(path: Path, sheets: dict[str, list[list]]) -> None:
    book = openpyxl.Workbook()
    book.remove(book.active)
    for title, rows in sheets.items():
        sheet = book.create_sheet(title)
        for row in rows:
            sheet.append(row)
    book.save(path)


def tables(out: Path) -> dict[str, list[tuple]]:
    with duckdb.connect(str(out / "raw.duckdb"), read_only=True) as connection:
        names = [row[0] for row in connection.execute("SHOW TABLES").fetchall()]
        # Names come from SHOW TABLES on a file this test wrote.
        query = 'SELECT * FROM "{}"'
        return {name: connection.execute(query.format(name)).fetchall() for name in names}


def by_name(report, name: str):
    return next(table for table in report.tables if table.name == name)


@pytest.fixture
def upload(tmp_path: Path) -> Path:
    path = tmp_path / "upload"
    path.mkdir()
    return path


@pytest.fixture
def out(tmp_path: Path) -> Path:
    path = tmp_path / "out"
    path.mkdir()
    return path


def test_csv_with_bom_keeps_text_and_reads_empty_fields_as_null(upload: Path, out: Path) -> None:
    (upload / "sales.csv").write_bytes('﻿Mã,Tên,Ghi chú\nA1,Cà phê,\nA2,"Trà, đá",""\n'.encode())

    report = ingest(upload, out)

    table = by_name(report, "sales")
    assert [c.name for c in table.columns] == ["Mã", "Tên", "Ghi chú", "_source", "_row"]
    assert tables(out)["sales"] == [
        ("A1", "Cà phê", None, "sales.csv", "2"),
        ("A2", "Trà, đá", None, "sales.csv", "3"),
    ]
    assert report.files[0].encoding == "utf-8-sig"


def test_csv_in_a_legacy_encoding_with_semicolons(upload: Path, out: Path) -> None:
    # cp1258 writes "ố" as "ô" (0xF4) plus a combining acute accent (0xEC).
    (upload / "old.csv").write_bytes(b"T\xean;S\xf4\xec\nC\xe0 ph\xea;1\n")

    report = ingest(upload, out)

    assert report.files[0].encoding == "cp1258"
    assert [c.name for c in by_name(report, "old").columns][:2] == ["Tên", "Số"]
    assert tables(out)["old"][0][:2] == ("Cà phê", "1")


def test_title_and_blank_rows_above_the_header_are_skipped(upload: Path, out: Path) -> None:
    rows = [["SỔ CHI PHÍ"], [], ["Ngày", "Mục", "Số tiền"], ["01/01/2025", "thuê", "25000k"]]
    write_xlsx(upload / "chi_phi.xlsx", {"Sheet1": rows})

    report = ingest(upload, out)

    table = by_name(report, "chi_phi")
    assert table.sources[0].header_row == 3
    assert table.sources[0].skipped_rows == 2
    assert tables(out)["chi_phi"] == [("01/01/2025", "thuê", "25000k", "chi_phi.xlsx#Sheet1", "4")]


def test_sheets_with_the_same_header_become_one_table(upload: Path, out: Path) -> None:
    header = ["Mã đơn", "Tổng"]
    write_xlsx(
        upload / "don.xlsx",
        {"T01": [header, ["D1", 10]], "T02": [header, ["D2", 20]], "Khac": [["X"], [1]]},
    )

    report = ingest(upload, out)

    assert sorted(t.name for t in report.tables) == ["don", "don_khac"]
    assert by_name(report, "don").row_count == 2
    assert [r[2] for r in tables(out)["don"]] == ["don.xlsx#T01", "don.xlsx#T02"]


def test_cell_values_become_lossless_text(upload: Path, out: Path) -> None:
    row = [dt.datetime(2025, 1, 2, 6, 9, 58), dt.datetime(2025, 1, 3), 3.0, 2.5, True, 45717]
    write_xlsx(upload / "v.xlsx", {"S": [["a", "b", "c", "d", "e", "f"], row]})

    ingest(upload, out)

    assert tables(out)["v"][0][:6] == (
        "2025-01-02 06:09:58",
        "2025-01-03",
        "3",
        "2.5",
        "true",
        "45717",
    )


def test_empty_and_duplicate_header_names_are_made_unique(upload: Path, out: Path) -> None:
    write_xlsx(upload / "d.xlsx", {"S": [["Tên", None, "Tên"], ["a", "b", "c", "extra"]]})

    report = ingest(upload, out)

    names = [c.name for c in by_name(report, "d").columns]
    assert names == ["Tên", "column_2", "Tên_2", "column_4", "_source", "_row"]


def test_junk_documents_and_unknown_files(upload: Path, out: Path) -> None:
    (upload / "a.csv").write_text("x\n1\n", encoding="utf-8")
    (upload / "a.csv:Zone.Identifier").write_text("[ZoneTransfer]", encoding="utf-8")
    (upload / "~$lock.xlsx").write_bytes(b"lock")
    (upload / "README.md").write_text("# Shop notes", encoding="utf-8")
    (upload / "photo.png").write_bytes(b"\x89PNG")

    report = ingest(upload, out)

    status = {f.path: f.status for f in report.files}
    assert status == {
        "a.csv": "loaded",
        "a.csv:Zone.Identifier": "ignored",
        "~$lock.xlsx": "ignored",
        "README.md": "document",
        "photo.png": "unsupported",
    }
    assert report.documents == [{"path": "README.md", "text": "# Shop notes"}]


def test_a_broken_file_is_reported_and_the_rest_still_loads(upload: Path, out: Path) -> None:
    (upload / "broken.xlsx").write_bytes(b"not a zip file")
    (upload / "ok.csv").write_text("x\n1\n", encoding="utf-8")

    report = ingest(upload, out)

    status = {f.path: f.status for f in report.files}
    assert status == {"broken.xlsx": "error", "ok.csv": "loaded"}
    assert "ok" in tables(out)


def test_oversized_files_are_rejected_before_parsing(upload: Path, out: Path) -> None:
    (upload / "big.csv").write_text("x\n" + "1\n" * 1000, encoding="utf-8")

    report = ingest(upload, out, IngestLimits(max_file_bytes=100))

    assert report.files[0].status == "rejected"
    assert "size" in (report.files[0].reason or "")


def test_a_zip_bomb_is_rejected(upload: Path, out: Path) -> None:
    with zipfile.ZipFile(upload / "bomb.xlsx", "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("xl/worksheets/sheet1.xml", "0" * 5_000_000)

    report = ingest(upload, out, IngestLimits(max_compression_ratio=100))

    assert report.files[0].status == "rejected"
    assert "compression" in (report.files[0].reason or "")


def test_row_limit_truncates_with_a_warning(upload: Path, out: Path) -> None:
    (upload / "many.csv").write_text("x\n" + "1\n" * 50, encoding="utf-8")

    report = ingest(upload, out, IngestLimits(max_rows_per_table=10))

    assert by_name(report, "many").row_count == 10
    assert by_name(report, "many").truncated


def test_sqlite_tables_are_read_as_text(upload: Path, out: Path) -> None:
    connection = sqlite3.connect(upload / "pos.sqlite")
    connection.execute("CREATE TABLE orders (id INTEGER, total REAL, note TEXT)")
    connection.execute("INSERT INTO orders VALUES (1, 2.5, NULL)")
    connection.commit()
    connection.close()

    report = ingest(upload, out)

    assert [t.name for t in report.tables] == ["orders"]
    assert tables(out)["orders"] == [("1", "2.5", None, "pos.sqlite#orders", "1")]


def test_profiles_describe_value_patterns(upload: Path, out: Path) -> None:
    rows = [
        ["22,000", "01/01/2025", "1 thg 1 2025", "25000k", "1 hộp"],
        ["5000", "02/01/2025", "2 thg 1 2025", "578.000đ", "2"],
    ]
    write_xlsx(upload / "p.xlsx", {"S": [["tien", "ngay", "ngay2", "chi", "sl"], *rows]})

    report = ingest(upload, out)

    profile = {c.name: c.profile for c in by_name(report, "p").columns}
    assert profile["tien"].patterns == {"number_grouped": 1, "integer": 1}
    assert profile["ngay"].patterns == {"date_dmy": 2}
    assert profile["ngay2"].patterns == {"date_text_vi": 2}
    assert profile["chi"].patterns == {"money_suffix": 2}
    assert profile["sl"].patterns == {"quantity_unit": 1, "integer": 1}
    assert profile["tien"].non_null == 2
    assert profile["tien"].distinct == 2
    assert set(profile["tien"].samples) == {"22,000", "5000"}


def test_profiles_hide_samples_of_personal_data(upload: Path, out: Path) -> None:
    rows = [["0912345678", "a@b.vn", "001099012345", "An"], ["0987654321", "c@d.vn", "", "Bình"]]
    write_xlsx(upload / "k.xlsx", {"S": [["dt", "mail", "so_giay", "CCCD"], *rows]})

    report = ingest(upload, out)

    profile = {c.name: c.profile for c in by_name(report, "k").columns}
    for name in ("dt", "mail", "so_giay", "CCCD"):
        assert profile[name].samples == [], name
        assert profile[name].sensitive, name


def test_unreadable_directories_are_reported(upload: Path, out: Path) -> None:
    (upload / "ok.csv").write_text("a\n1\n", encoding="utf-8")
    locked = upload / "locked"
    locked.mkdir()
    (locked / "x.csv").write_text("a\n1\n", encoding="utf-8")
    locked.chmod(0o000)
    try:
        report = ingest(upload, out)
    finally:
        locked.chmod(0o755)

    assert [t.name for t in report.tables] == ["ok"]
    assert any("locked" in warning for warning in report.warnings)


def test_large_round_numbers_are_not_mistaken_for_id_numbers(upload: Path, out: Path) -> None:
    write_xlsx(upload / "s.xlsx", {"S": [["Tồn lớn nhất"], ["999999999"], ["999999999"]]})

    report = ingest(upload, out)

    profile = by_name(report, "s").columns[0].profile
    assert not profile.sensitive
    assert profile.samples == ["999999999"]


def test_rare_sparse_rows_are_reported_as_shapes(upload: Path, out: Path) -> None:
    lines = ["Mã,Sản phẩm,Tiền,SĐT,Ghi chú"]
    lines += [f"D{i},Trà,{i},09123456{i:02d},{'gấp' if i % 3 == 0 else ''}" for i in range(40)]
    lines += [",,780,0912345699,", "Tổng tháng,,,,"]
    (upload / "t.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")

    report = ingest(upload, out)

    shapes = by_name(report, "t").rare_shapes
    assert [(s.columns, s.rows) for s in shapes] == [(["Tiền", "SĐT"], 1), (["Mã"], 1)]
    assert shapes[0].examples == [{"_row": "42", "Tiền": "780", "SĐT": "<sensitive>"}]


def test_tables_with_one_row_shape_report_no_rare_shapes(upload: Path, out: Path) -> None:
    (upload / "t.csv").write_text("a,b\n1,2\n3,4\n", encoding="utf-8")

    report = ingest(upload, out)

    assert by_name(report, "t").rare_shapes == []


def daily_sales() -> str:
    lines = ["Ngày,Sản phẩm,Tiền"]
    lines += ["01/03/2025,Trà,1.000"] * 40
    lines += [',Tổng ngày 01/03,"40,000"']
    lines += ["02/03/2025,Cà phê,2500"] * 30
    lines += [",Tổng ngày 02/03,75000", ",Tổng tháng 3,115000", "Ghi chú: tiền tính bằng đồng,,"]
    return "\n".join(lines) + "\n"


def test_rare_rows_that_equal_the_sum_of_the_rows_above_are_counted(
    upload: Path, out: Path
) -> None:
    (upload / "t.csv").write_text(daily_sales(), encoding="utf-8")

    report = ingest(upload, out)

    shapes = {tuple(s.columns): s for s in by_name(report, "t").rare_shapes}
    assert (shapes[("Sản phẩm", "Tiền")].rows, shapes[("Sản phẩm", "Tiền")].sums_above) == (3, 2)
    assert shapes[("Ngày",)].sums_above == 0


def test_hostile_column_names_stay_data_in_row_shapes(upload: Path, out: Path) -> None:
    hostile = 'x"); DROP TABLE t; --'
    header = f'"{hostile.replace(chr(34), chr(34) * 2)}",b'
    lines = [header] + ["1,2"] * 40 + [",3", ",4"]
    (upload / "t.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")

    report = ingest(upload, out)

    [shape] = by_name(report, "t").rare_shapes
    assert (shape.columns, shape.rows) == (["b"], 2)
    assert len(tables(out)["t"]) == 42
