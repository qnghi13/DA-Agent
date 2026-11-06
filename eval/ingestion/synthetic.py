"""Synthetic uploads with labelled subtotal, total, and note rows, built from no real data.

They check that exclusion generalises beyond the DataMate dev shops: other languages, totals above
their group, notes mid-table, a non-retail table, a share of summary rows above the rare-shape
cut-off, and a clean dataset whose names look like totals ("Tống", "Total Tools") and whose rare
sparse rows are real records. Output is deterministic.

uv run python -m eval.ingestion.synthetic --out eval/reports/synthetic/uploads \\
    --labels eval/reports/synthetic/labels.jsonl
"""

import argparse
import csv
import json
import random
import sys
from dataclasses import dataclass, field
from pathlib import Path

import openpyxl

Cell = str | int | None
SEED = 20261106
PRODUCTS = [
    ("Total Tools kìm điện 8 inch", 145000),
    ("Bộ cộng lực 1/2 inch", 320000),
    ("Cà phê sữa đá", 29000),
    ("Bánh mì thịt", 25000),
    ("Paint roller 9in", 89000),
    ("LED bulb 12W", 45000),
    ("Bút bi xanh", 5000),
    ("Sổ tay A5", 32000),
]
CUSTOMERS = ["Tống Văn An", "Tong Minh Chau", "Nguyễn Thị Hoa", "Alice Brown", "Trần Quốc Bảo"]


@dataclass
class Sheet:
    header: list[str]
    rows: list[list[Cell]] = field(default_factory=list)
    kinds: list[str | None] = field(default_factory=list)

    def add(self, row: list[Cell], kind: str | None = None) -> None:
        self.rows.append(row)
        self.kinds.append(kind)


def _labels(dataset: str, source: str, sheet: Sheet) -> list[dict]:
    # The header is row 1, so the first data row is row 2.
    return [
        {"dataset": dataset, "source": source, "row": index + 2, "kind": kind}
        for index, kind in enumerate(sheet.kinds)
        if kind is not None
    ]


def _write_csv(path: Path, sheet: Sheet) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(sheet.header)
        writer.writerows([["" if c is None else c for c in row] for row in sheet.rows])


def _write_xlsx(path: Path, sheets: dict[str, Sheet]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    book = openpyxl.Workbook()
    book.remove(book.active)
    for title, sheet in sheets.items():
        target = book.create_sheet(title)
        target.append(sheet.header)
        for row in sheet.rows:
            target.append(row)
    book.save(path)


def _en_sales(rng: random.Random) -> Sheet:
    """English daily sales with a subtotal after each day and a grand total at the end."""
    sheet = Sheet(["Date", "Invoice", "Customer", "Product", "Qty", "Unit price", "Amount"])
    invoice, grand = 1000, 0
    for day in range(1, 31):
        total = 0
        for _ in range(rng.randint(18, 30)):
            product, price = rng.choice(PRODUCTS)
            qty = rng.randint(1, 5)
            invoice += 1
            sheet.add(
                [f"2025-03-{day:02d}", f"INV{invoice}", rng.choice(CUSTOMERS), product, qty,
                 price, qty * price]
            )  # fmt: skip
            total += qty * price
        sheet.add([None, None, None, f"Subtotal 2025-03-{day:02d}", None, None, total], "subtotal")
        grand += total
    sheet.add([None, None, None, "Grand total", None, None, grand], "total")
    return sheet


def _total_above(rng: random.Random) -> Sheet:
    """Each day starts with a heading row holding the date and the day's total. Line rows leave
    the date blank, so it must be filled down from the heading."""
    sheet = Sheet(["Ngày", "Mã đơn", "Sản phẩm", "SL", "Thành tiền"])
    order = 0
    for day in range(1, 21):
        lines = []
        for _ in range(rng.randint(15, 25)):
            product, price = rng.choice(PRODUCTS)
            qty = rng.randint(1, 4)
            order += 1
            lines.append([None, f"DH{order:05d}", product, qty, f"{qty * price:,}"])
        total = sum(int(str(line[4]).replace(",", "")) for line in lines)
        sheet.add([f"{day:02d}/04/2025", None, "Tổng ngày", None, f"{total:,}"], "subtotal")
        for line in lines:
            sheet.add(line)
    return sheet


def _notes_mid(rng: random.Random) -> dict[str, Sheet]:
    """Stock movements per month, with note rows mid-table and a total row at each sheet's end."""
    sheets = {}
    for month in (5, 6):
        sheet = Sheet(["Mã phiếu", "Mặt hàng", "Nhập", "Xuất", "Giá trị"])
        incoming = outgoing = value = 0
        note_at = set(rng.sample(range(20, 140), 3))
        for index in range(150):
            if index in note_at:
                sheet.add(["Lưu ý: hàng đổi trả ghi số âm", None, None, None, None], "note")
            product, price = rng.choice(PRODUCTS)
            add, take = rng.choice([(rng.randint(1, 20), 0), (0, rng.randint(1, 10))])
            sheet.add([f"PK{month}{index:04d}", product, add, take, (add - take) * price])
            incoming, outgoing, value = (
                incoming + add,
                outgoing + take,
                value + (add - take) * price,
            )
        sheet.add([None, "Cộng", incoming, outgoing, value], "total")
        sheets[f"Tháng {month}"] = sheet
    return sheets


def _clean_names(rng: random.Random) -> Sheet:
    """No summary rows. About 3% of orders are walk-ins with no customer or phone: sparse and
    rare, but real records."""
    sheet = Sheet(["Mã đơn", "Ngày", "Khách hàng", "SĐT", "Sản phẩm", "SL", "Thành tiền"])
    for index in range(600):
        product, price = rng.choice(PRODUCTS)
        qty = rng.randint(1, 3)
        walk_in = rng.random() < 0.03
        customer = None if walk_in else rng.choice(CUSTOMERS)
        phone = None if walk_in else f"09{rng.randint(10_000_000, 99_999_999)}"
        sheet.add(
            [f"HD{index:05d}", f"{rng.randint(1, 28):02d}/07/2025", customer, phone, product, qty,
             qty * price]
        )  # fmt: skip
    return sheet


def _clinic(rng: random.Random) -> Sheet:
    """Clinic visits with a subtotal per doctor and day, about 9% of rows, and a grand total."""
    sheet = Sheet(["Ngày khám", "Bác sĩ", "Mã BN", "Dịch vụ", "Phí"])
    services = [("Khám tổng quát", 200000), ("Siêu âm", 350000), ("Xét nghiệm máu", 180000)]
    doctors = ["BS Lan", "BS Hùng", "BS Mai", "BS Quân"]
    grand = 0
    for day in range(1, 16):
        for doctor in doctors:
            total = 0
            for _ in range(rng.randint(7, 13)):
                service, fee = rng.choice(services)
                sheet.add([f"{day:02d}/08/2025", doctor, f"BN{rng.randint(1000, 9999)}", service,
                           fee])  # fmt: skip
                total += fee
            sheet.add([f"{day:02d}/08/2025", f"Tổng {doctor}", None, None, total], "subtotal")
            grand += total
    sheet.add([None, "TỔNG CỘNG", None, None, grand], "total")
    return sheet


def write_synthetic(root: Path) -> list[dict]:
    """Write one directory per dataset under root and return the labelled rows."""
    rng = random.Random(SEED)  # noqa: S311 - reproducible test data, not secrets
    labels: list[dict] = []

    sheet = _en_sales(rng)
    _write_csv(root / "syn_en_sales" / "sales_march.csv", sheet)
    labels += _labels("syn_en_sales", "sales_march.csv", sheet)

    sheet = _total_above(rng)
    _write_xlsx(root / "syn_total_above" / "BanHang.xlsx", {"Sheet1": sheet})
    labels += _labels("syn_total_above", "BanHang.xlsx#Sheet1", sheet)

    sheets = _notes_mid(rng)
    _write_xlsx(root / "syn_notes_mid" / "NhapXuat.xlsx", sheets)
    for title, sheet in sheets.items():
        labels += _labels("syn_notes_mid", f"NhapXuat.xlsx#{title}", sheet)

    _write_csv(root / "syn_clean_names" / "don_hang.csv", _clean_names(rng))

    sheet = _clinic(rng)
    _write_xlsx(root / "syn_clinic" / "LuotKham.xlsx", {"Sheet1": sheet})
    labels += _labels("syn_clinic", "LuotKham.xlsx#Sheet1", sheet)
    return labels


def main() -> int:
    parser = argparse.ArgumentParser(description="Write synthetic uploads and their labels.")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--labels", type=Path, required=True)
    args = parser.parse_args()
    labels = write_synthetic(args.out)
    args.labels.parent.mkdir(parents=True, exist_ok=True)
    args.labels.write_text(
        "".join(json.dumps(label, ensure_ascii=False) + "\n" for label in labels), encoding="utf-8"
    )
    print(f"{len(labels)} labelled rows in {len({label['dataset'] for label in labels})} datasets")
    return 0


if __name__ == "__main__":
    sys.exit(main())
