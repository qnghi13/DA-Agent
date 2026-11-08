"""Generate the demo dataset: a small shop's orders, with the mess real exports have.

    uv run python examples/generate.py

Writes examples/demo-shop/orders.csv and products.csv. The data is synthetic and seeded, so the
files are the same on every run and the expected answers below stay true:

- Revenue (quantity x unit price of "completed" orders) in June 2026: printed at the end.
- The TOTAL row at the bottom of orders.csv must not be counted.
"""

import csv
import random
from datetime import date, timedelta
from pathlib import Path

OUT = Path(__file__).parent / "demo-shop"
SEED = 20260601
PRODUCTS = [
    ("P01", "Cà phê sữa đá", "Đồ uống", 29_000),
    ("P02", "Trà đào cam sả", "Đồ uống", 35_000),
    ("P03", "Bánh mì thịt", "Đồ ăn", 25_000),
    ("P04", "Bánh croissant", "Đồ ăn", 32_000),
    ("P05", "Nước ép cam", "Đồ uống", 39_000),
    ("P06", "Bánh flan", "Tráng miệng", 18_000),
]
STATUSES = ["completed"] * 8 + ["cancelled", "refunded"]
CHANNELS = ["Tại quán", "GrabFood", "ShopeeFood"]


def money(amount: int) -> str:
    """Vietnamese exports often write 29000 as "29.000"."""
    return f"{amount:,}".replace(",", ".")


def main() -> None:
    rng = random.Random(SEED)  # noqa: S311 - demo data, not security
    OUT.mkdir(exist_ok=True)
    with (OUT / "products.csv").open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(["product_id", "name", "category", "list_price"])
        for product in PRODUCTS:
            writer.writerow(product)

    start = date(2026, 4, 1)
    rows, june_revenue, total_quantity = [], 0, 0
    for number in range(1, 601):
        day = start + timedelta(days=rng.randrange(91))
        product_id, _, _, price = rng.choice(PRODUCTS)
        quantity = rng.choice([1, 1, 1, 2, 2, 3])
        status = rng.choice(STATUSES)
        total_quantity += quantity
        if status == "completed" and day.month == 6:
            june_revenue += quantity * price
        rows.append(
            [
                f"DH{number:04d}",
                day.strftime("%d/%m/%Y"),
                product_id,
                quantity,
                money(price),
                rng.choice(CHANNELS),
                status,
            ]
        )
    rows.sort(key=lambda row: (row[1][6:], row[1][3:5], row[1][:2], row[0]))
    with (OUT / "orders.csv").open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(
            ["Mã đơn", "Ngày", "Mã SP", "Số lượng", "Đơn giá", "Kênh bán", "Trạng thái"]
        )
        writer.writerows(rows)
        writer.writerow(["TOTAL", "", "", total_quantity, "", "", ""])
    print(f"wrote {OUT}; completed revenue in June 2026: {june_revenue:,} VND")


if __name__ == "__main__":
    main()
