"""A synthetic shop with planted effects, and questions whose answers follow from them (ADR 0051).

    uv run python -m eval.analysis.generate [--out eval/analysis]

Writes OUT/shop/orders.csv and OUT/questions.jsonl. The data is seeded, so every run writes the
same bytes. It holds no DataMate data. The planted effects:

- October 2026 against September: revenue falls, and branch Q1 carries the drop (food most).
- 1-26 November against 1-26 October: GrabFood halves, so the channel leads the change.
- The drinks category grows every month; every December sells more.
- Three days sell five times a normal day.
- Bigger discounts come with bigger quantities.

Expected values come from these rows and from the same analysis code the agent's tool runs, so a
question checks whether the agent asks for the right analysis, not whether the method is right:
the method has its own tests.
"""

import argparse
import csv
import datetime as dt
import json
import math
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import duckdb

from da_agent.storage.stats import run_stats

SEED = 20261126
AS_OF = dt.date(2026, 11, 26)
START = dt.date(2024, 6, 1)
SHOP = "shop"
ORDERS = "orders.csv"
QUESTIONS = "questions.jsonl"
COLUMNS = [
    "order_id", "order_date", "branch", "category", "channel", "quantity", "unit_price",
    "discount_percent", "status", "amount",
]  # fmt: skip
BRANCHES = ("Q1", "Q3", "Q7")
CHANNELS = ("Tại quán", "GrabFood", "ShopeeFood")
PRICES = {"Đồ uống": 30_000, "Đồ ăn": 45_000, "Tráng miệng": 25_000, "Bánh": 35_000}
OUTLIER_DAYS = (dt.date(2025, 3, 14), dt.date(2025, 8, 9), dt.date(2026, 2, 21))
DISCOUNTS = (0, 0, 0, 5, 10, 15, 20)
# Orders per day for one branch, channel, and category, before the planted effects.
BASE_RATE = 0.6
DOCUMENT = (
    "One row is one order. Revenue is the sum of amount over completed orders; cancelled "
    "orders are not revenue. amount is quantity x unit_price after the discount."
)


def _rate(day: dt.date, branch: str, channel: str, category: str) -> float:
    months = (day.year - START.year) * 12 + day.month - START.month
    rate = BASE_RATE
    if category == "Đồ uống":
        rate *= 1 + 0.03 * months
    if day.month == 12:
        rate *= 1.5
    if (day.year, day.month) in ((2026, 10), (2026, 11)) and branch == "Q1":
        rate *= 0.2 if category == "Đồ ăn" else 0.7
    if (day.year, day.month) == (2026, 11) and channel == "GrabFood":
        rate *= 0.5
    if day in OUTLIER_DAYS:
        rate *= 5
    return rate


def _count(rng: random.Random, rate: float) -> int:
    """A count with mean rate and some spread: rate split over twice as many tries."""
    tries = max(1, math.ceil(rate * 2))
    return sum(1 for _ in range(tries) if rng.random() < rate / tries)


def orders() -> list[dict[str, Any]]:
    rng = random.Random(SEED)  # noqa: S311 - synthetic data, not security
    rows: list[dict[str, Any]] = []
    day = START
    while day <= AS_OF:
        for branch in BRANCHES:
            for channel in CHANNELS:
                for category, price in PRICES.items():
                    for _ in range(_count(rng, _rate(day, branch, channel, category))):
                        discount = rng.choice(DISCOUNTS)
                        quantity = 1 + (discount >= 10) + (discount >= 20) + (rng.random() < 0.3)
                        rows.append(
                            {
                                "order_id": f"D{len(rows) + 1:06d}",
                                "order_date": day.isoformat(),
                                "branch": branch,
                                "category": category,
                                "channel": channel,
                                "quantity": quantity,
                                "unit_price": price,
                                "discount_percent": discount,
                                "status": "cancelled" if rng.random() < 0.08 else "completed",
                                "amount": round(quantity * price * (1 - discount / 100)),
                            }
                        )
        day += dt.timedelta(days=1)
    return rows


# Expected answers


def _database(path: Path, orders_csv: Path) -> Path:
    types = {
        "order_id": "VARCHAR", "order_date": "DATE", "branch": "VARCHAR", "category": "VARCHAR",
        "channel": "VARCHAR", "quantity": "INTEGER", "unit_price": "BIGINT",
        "discount_percent": "INTEGER", "status": "VARCHAR", "amount": "BIGINT",
    }  # fmt: skip
    with duckdb.connect(str(path)) as connection:
        connection.execute(
            "CREATE TABLE orders AS SELECT * FROM read_csv(?, header = true, columns = ?)",
            [str(orders_csv), types],
        )
    return path


def _stats(database: Path, kind: str, sql: str, x: str, y: str, **options: Any) -> dict[str, Any]:
    return run_stats(database, kind, sql, x, y, data_start=START, data_end=AS_OF, **options)


def _span(start: str, end: str) -> tuple[dt.date, dt.date]:
    return dt.date.fromisoformat(start), dt.date.fromisoformat(end)


def _top(result: dict[str, Any]) -> dict[str, str]:
    top = result["summary"]["top"]
    return {"type": "top_segment", "dimension": top["dimension"], "segment": top["segment"]}


def _first_forecast(result: dict[str, Any]) -> float:
    return next(row[2] for row in result["table"]["rows"] if row[1] is None)


DAILY = (
    "SELECT order_date AS day, branch, category, channel, SUM(amount) AS revenue FROM orders "
    "WHERE status = 'completed' GROUP BY ALL"
)
MONTHLY = (
    "SELECT date_trunc('month', order_date) AS month, SUM(amount) AS revenue FROM orders "
    "WHERE status = 'completed'{where} GROUP BY 1 ORDER BY 1"
)


def _questions(database: Path, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sep, oct_ = _span("2026-09-01", "2026-09-30"), _span("2026-10-01", "2026-10-31")
    oct_same, nov = _span("2026-10-01", "2026-10-26"), _span("2026-11-01", "2026-11-26")
    dims = ["branch", "channel", "category"]
    october = _stats(database, "explain_change", DAILY, "day", "revenue",
                     dimensions=dims, before=sep, after=oct_)  # fmt: skip
    november = _stats(database, "explain_change", DAILY, "day", "revenue",
                      dimensions=dims, before=oct_same, after=nov)  # fmt: skip
    q1_sql = DAILY.replace("WHERE", "WHERE branch = 'Q1' AND")
    q1 = _stats(database, "explain_change", q1_sql, "day", "revenue",
                dimensions=["category", "channel"], before=sep, after=oct_)  # fmt: skip
    drinks = _stats(database, "trend", MONTHLY.format(where=" AND category = 'Đồ uống'"),
                    "month", "revenue")  # fmt: skip
    desserts = _stats(database, "trend", MONTHLY.format(where=" AND category = 'Tráng miệng'"),
                      "month", "revenue")  # fmt: skip
    revenue_ahead = _stats(database, "forecast", MONTHLY.format(where=""), "month", "revenue",
                           periods=3)  # fmt: skip
    counts_sql = (
        "SELECT date_trunc('month', order_date) AS month, COUNT(*) AS orders FROM orders "
        "WHERE status = 'completed' GROUP BY 1 ORDER BY 1"
    )
    orders_ahead = _stats(database, "forecast", counts_sql, "month", "orders", periods=1)
    correlation = _stats(database, "correlation",
                         "SELECT discount_percent, quantity FROM orders", "discount_percent",
                         "quantity")  # fmt: skip
    outliers = _stats(database, "outliers",
                      "SELECT order_date AS day, SUM(amount) AS revenue FROM orders WHERE "
                      "status = 'completed' GROUP BY 1", "day", "revenue")  # fmt: skip
    found_days = {row[0] for row in outliers["table"]["rows"]}
    planted = sorted(d.isoformat() for d in OUTLIER_DAYS)
    if not set(planted) <= found_days:
        raise RuntimeError(f"planted outlier days not found: {planted} vs {sorted(found_days)}")

    def ranges(before: tuple[dt.date, dt.date], after: tuple[dt.date, dt.date]) -> dict:
        def shown(span: tuple[dt.date, dt.date]) -> dict[str, str]:
            return {"start": span[0].isoformat(), "end": span[1].isoformat()}

        return {"type": "ranges", "before": shown(before), "after": shown(after)}

    completed = [row for row in rows if row["status"] == "completed"]

    def revenue(where: Any) -> int:
        return sum(row["amount"] for row in completed if where(row))

    channel_2025: Counter[str] = Counter()
    for row in completed:
        if row["order_date"].startswith("2025"):
            channel_2025[row["channel"]] += row["amount"]
    october_orders = [r for r in completed if r["order_date"].startswith("2026-10")]
    number = "number"
    return [
        {"id": "change-oct", "question": "Vì sao doanh thu tháng 10/2026 giảm so với tháng 9/2026?",
         "analysis": "explain_change", "checks": [_top(october), ranges(sep, oct_)]},
        {"id": "change-mtd",
         "question": "Doanh thu tháng này so với cùng kỳ tháng trước thay đổi thế nào, do đâu?",
         "analysis": "explain_change", "checks": [_top(november), ranges(oct_same, nov)]},
        {"id": "change-q1",
         "question": (
             "Doanh thu chi nhánh Q1 giảm từ tháng 9 sang tháng 10/2026 chủ yếu ở nhóm hàng nào?"
         ),
         "analysis": "explain_change", "checks": [_top(q1)]},
        {"id": "change-en",
         "question": "Why did revenue drop in October 2026 compared with September 2026?",
         "analysis": "explain_change", "checks": [_top(october)]},
        {"id": "trend-drinks", "question": "Doanh thu nhóm Đồ uống theo tháng đang tăng hay giảm?",
         "analysis": "trend",
         "checks": [{"type": "direction", "value": drinks["summary"]["direction"]}]},
        {"id": "trend-desserts",
         "question": "Doanh thu nhóm Tráng miệng theo tháng có xu hướng tăng hay giảm không?",
         "analysis": "trend",
         "checks": [{"type": "direction", "value": desserts["summary"]["direction"]}]},
        {"id": "forecast-revenue", "question": "Dự báo doanh thu 3 tháng tới.",
         "analysis": "forecast",
         "checks": [{"type": "forecast_near", "value": _first_forecast(revenue_ahead),
                     "tolerance": 0.2}]},
        {"id": "forecast-orders",
         "question": "Forecast the number of completed orders next month.",
         "analysis": "forecast",
         "checks": [{"type": "forecast_near", "value": _first_forecast(orders_ahead),
                     "tolerance": 0.2}]},
        {"id": "correlation-discount",
         "question": "Giảm giá nhiều hơn thì khách mua số lượng nhiều hơn không?",
         "analysis": "correlation",
         "checks": [{"type": "correlation",
                     "sign": "positive" if correlation["summary"]["pearson"] > 0 else "negative",
                     "min_strength": "moderate"}]},
        {"id": "outliers-days", "question": "Có ngày nào doanh thu cao bất thường không?",
         "analysis": "outliers", "checks": [{"type": "contains", "values": planted}]},
        {"id": "control-sep", "question": "Tổng doanh thu tháng 9/2026 là bao nhiêu?",
         "analysis": "none",
         "checks": [{"type": number, "tolerance": 0.005,
                     "value": revenue(lambda r: r["order_date"].startswith("2026-09"))}]},
        {"id": "control-q3",
         "question": "Chi nhánh Q3 có bao nhiêu đơn hoàn thành trong tháng 8/2026?",
         "analysis": "none",
         "checks": [{"type": number, "tolerance": 0.0, "value": sum(
             1 for r in completed
             if r["branch"] == "Q3" and r["order_date"].startswith("2026-08"))}]},
        {"id": "control-channel", "question": "Kênh bán nào có doanh thu cao nhất năm 2025?",
         "analysis": "none",
         "checks": [{"type": "text_contains", "value": channel_2025.most_common(1)[0][0]}]},
        {"id": "control-aov",
         "question": "Giá trị trung bình mỗi đơn hoàn thành trong tháng 10/2026 là bao nhiêu?",
         "analysis": "none",
         "checks": [{"type": number, "tolerance": 0.005, "value": round(
             sum(r["amount"] for r in october_orders) / len(october_orders), 2)}]},
        {"id": "control-cancelled", "question": "Có bao nhiêu đơn bị hủy trong tháng 7/2026?",
         "analysis": "none",
         "checks": [{"type": number, "tolerance": 0.0, "value": sum(
             1 for r in rows
             if r["status"] == "cancelled" and r["order_date"].startswith("2026-07"))}]},
        {"id": "control-year", "question": "What was the total revenue in 2025?",
         "analysis": "none",
         "checks": [{"type": number, "tolerance": 0.005,
                     "value": revenue(lambda r: r["order_date"].startswith("2025"))}]},
    ]  # fmt: skip


def generate(out: Path) -> dict[str, Any]:
    """Write the shop and its questions under out; return a summary of what was planted."""
    rows = orders()
    shop = out / SHOP
    shop.mkdir(parents=True, exist_ok=True)
    with (shop / ORDERS).open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    database = _database(out / "expected.duckdb", shop / ORDERS)
    try:
        questions = _questions(database, rows)
    finally:
        database.unlink(missing_ok=True)
    with (out / QUESTIONS).open("w", encoding="utf-8") as file:
        for question in questions:
            line = {"id": question["id"], "question": question["question"],
                    "as_of": AS_OF.isoformat(), "analysis": question["analysis"],
                    "checks": question["checks"]}  # fmt: skip
            file.write(json.dumps(line, ensure_ascii=False) + "\n")
    by_status = Counter(row["status"] for row in rows)
    per_month: dict[str, int] = defaultdict(int)
    for row in rows:
        per_month[row["order_date"][:7]] += 1
    return {"orders": len(rows), "status": dict(by_status), "months": len(per_month)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Write the synthetic shop and its questions.")
    parser.add_argument("--out", type=Path, default=Path("eval/analysis"))
    args = parser.parse_args()
    summary = generate(args.out)
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
