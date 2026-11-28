"""Generate a synthetic upload with layouts the dev shops lack, and its layout targets.

The layouts: a timesheet whose rows come in In and Out pairs per person and month, split by a
department row, with days as columns; a monthly calendar whose cells hold several
`key: value | key: value` records, one per line; a two-column list of abbreviations where many
rows fill one cell; and a monthly sales book that ends in a total written as compact money
(`128tr450`). Data is made up and seeded, so the upload and its
targets can be committed and regenerated.

uv run python -m eval.ingestion.layout_synthetic --output-dir eval/reports/layout-synthetic
"""

import argparse
import json
import random
import sys
from pathlib import Path

import openpyxl

SHOP = "syn_layouts"
DEPARTMENTS = ("SALES", "WAREHOUSE", "OFFICE")
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
FLOWERS = ("Red rose", "White lily", "Daisy", "Orchid", "Tulip")
CUSTOMERS = ("An", "Binh", "Chi", "Dung", "Giang", "Hoa", "Khanh", "Lan")


def _time(rng: random.Random, hour: int) -> str:
    return f"{hour:02d}:{rng.randint(0, 59):02d}"


def timesheet(rng: random.Random) -> tuple[list[list[object]], list[list[str]]]:
    """The sheet's rows and its tidy records: (code, month, day, in, out)."""
    rows: list[list[object]] = [
        ["TIMESHEET"],
        ["In and out times; OFF: day off"],
        ["Code", "Name", "Department", "Month", "In/Out", *[str(d) for d in range(1, 32)]],
    ]
    tidy: list[list[str]] = []
    person = 0
    for department in DEPARTMENTS:
        rows.append([f"DEPARTMENT {department}"])
        for _ in range(3):
            person += 1
            code = f"E{person:03d}"
            for month, days in (("2026-01", 31), ("2026-02", 28)):
                ins: list[object] = []
                outs: list[object] = []
                for day in range(1, days + 1):
                    roll = rng.random()
                    if roll < 0.15:
                        ins.append(None)
                        outs.append(None)
                    elif roll < 0.2:
                        ins.append("OFF")
                        outs.append("OFF")
                    else:
                        ins.append(_time(rng, 7))
                        outs.append(_time(rng, 16))
                    if ins[-1] is not None:
                        tidy.append([code, month, str(day), str(ins[-1]), str(outs[-1])])
                rows.append([code, f"Person {person}", department, month, "In", *ins])
                rows.append([None, None, None, None, "Out", *outs])
    return rows, tidy


def calendar(rng: random.Random) -> tuple[list[list[object]], list[list[str]]]:
    """A month of orders by week and weekday; tidy records: (week, weekday, order, customer)."""
    rows: list[list[object]] = [["Week", *WEEKDAYS]]
    tidy: list[list[str]] = []
    order = 0
    for week in range(1, 6):
        cells: list[object] = [f"Week {week}"]
        for weekday in WEEKDAYS:
            lines = []
            for _ in range(rng.randint(0, 3)):
                order += 1
                customer = rng.choice(CUSTOMERS)
                note = " | Note: deliver before noon" if rng.random() < 0.3 else ""
                lines.append(
                    f"Order: {order} | Customer: {customer} | Flower: {rng.choice(FLOWERS)} "
                    f"| Qty: {rng.randint(1, 20)}{note}"
                )
                tidy.append([f"Week {week}", weekday, str(order), customer])
            cells.append("\n".join(lines) or None)
        rows.append(cells)
    return rows, tidy


def abbreviations(rng: random.Random) -> tuple[list[list[object]], list[list[str]]]:
    rows: list[list[object]] = [["Short", "Full name"]]
    for n in range(20):
        short, full = f"AB{n:02d}", f"{rng.choice(FLOWERS)} box {n}"
        rows.append([f"{short} = {full}"] if n % 2 else [short, full])
    tidy = [[str(c) for c in r if c] for r in rows[1:]]
    return rows, tidy


def _compact(amount: int) -> str:
    """Money as a shop writes it: 149966000 -> 128tr450."""
    millions, rest = divmod(amount // 1000, 1000)
    return f"{millions}tr{rest:03d}".rstrip("0") if rest else f"{millions}tr"


def sales_book(rng: random.Random, month: int) -> tuple[list[list[object]], list[list[str]]]:
    rows: list[list[object]] = [
        [f"SALES BOOK MONTH {month}/2025"],
        ["Day", "Group", "Item", "Qty", "Price", "Total"],
    ]
    tidy: list[list[str]] = []
    total = 0
    for day in range(1, 11):
        for k in range(rng.randint(1, 3)):
            qty, price = rng.randint(1, 9), rng.choice((12, 25, 60, 120)) * 1000
            total += qty * price
            item = rng.choice(FLOWERS)
            rows.append([f"{day}/{month}" if k == 0 else None, "FLOWERS", item, qty,
                         f"{price // 1000}k", qty * price])  # fmt: skip
            tidy.append([str(c) for c in rows[-1] if c is not None][:3])
    # The book's own total, rounded by hand, so it does not equal the sum of the rows.
    rows.append([None, None, None, None, None, _compact(total + 7_000)])
    return rows, tidy


def _pick(items: list[list[str]]) -> list[list[str]]:
    return [items[0], items[len(items) // 2], items[-1]]


def generate(output_dir: Path, seed: int = 20261128) -> Path:
    """Write `<output>/uploads/<shop>/` and `<output>/targets.jsonl`; return the uploads root."""
    rng = random.Random(seed)  # noqa: S311 - reproducible test data, not secrets
    upload = output_dir / "uploads" / SHOP
    upload.mkdir(parents=True, exist_ok=True)
    book = openpyxl.Workbook()
    for default in book.worksheets:
        book.remove(default)
    sheet_rows, sheet_tidy = timesheet(rng)
    sheet = book.create_sheet("Timesheet")
    for row in sheet_rows:
        sheet.append(row)
    targets = [
        {"dataset": SHOP, "source": "HR.xlsx#Timesheet", "rows": len(sheet_tidy),
         "samples": _pick(sheet_tidy)}
    ]  # fmt: skip
    book.save(upload / "HR.xlsx")

    orders = openpyxl.Workbook()
    for default in orders.worksheets:
        orders.remove(default)
    for month in ("T5.2026", "T6.2026"):
        cal_rows, cal_tidy = calendar(rng)
        sheet = orders.create_sheet(month)
        for row in cal_rows:
            sheet.append(row)
        samples = [[*s, month] for s in _pick(cal_tidy)]
        targets.append(
            {"dataset": SHOP, "source": f"Orders.xlsx#{month}", "rows": len(cal_tidy),
             "samples": samples}
        )  # fmt: skip
    orders.save(upload / "Orders.xlsx")

    lists = openpyxl.Workbook()
    for default in lists.worksheets:
        lists.remove(default)
    abbr_rows, abbr_tidy = abbreviations(rng)
    sheet = lists.create_sheet("Abbreviations")
    for row in abbr_rows:
        sheet.append(row)
    targets.append(
        {"dataset": SHOP, "source": "Lists.xlsx#Abbreviations", "rows": len(abbr_tidy),
         "samples": _pick(abbr_tidy)}
    )  # fmt: skip
    lists.save(upload / "Lists.xlsx")

    sales = openpyxl.Workbook()
    for default in sales.worksheets:
        sales.remove(default)
    for month in (3, 4):
        book_rows, book_tidy = sales_book(rng, month)
        sheet = sales.create_sheet(f"T{month}.2025")
        for row in book_rows:
            sheet.append(row)
        targets.append(
            {"dataset": SHOP, "source": f"Sales.xlsx#T{month}.2025", "rows": len(book_tidy),
             "samples": _pick(book_tidy)}
        )  # fmt: skip
    sales.save(upload / "Sales.xlsx")
    lines = [json.dumps(t, ensure_ascii=False) for t in targets]
    (output_dir / "targets.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output_dir / "uploads"


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the synthetic layout upload.")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20261128)
    args = parser.parse_args()
    print(generate(args.output_dir, args.seed))
    return 0


if __name__ == "__main__":
    sys.exit(main())
