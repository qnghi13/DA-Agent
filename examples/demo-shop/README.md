# Demo shop

A small café's sales from April to June 2026, exported from its point-of-sale system. Synthetic,
made by `examples/generate.py`.

- `orders.csv`: one row per order line. Dates are day/month/year. Prices are in VND with dots as
  thousands separators. Revenue counts only `completed` orders: quantity × unit price. The last
  row is a total and is not an order.
- `products.csv`: the product list with categories.

Questions to try: "What was the revenue in June 2026?", "Which product sold the most units?",
"Revenue by sales channel per month, as a chart".
