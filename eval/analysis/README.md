# Fixed-analysis eval

Measures the `analyze` tool ([ADR 0051](../../docs/adr/0051-fixed-analyses.md)) on a synthetic shop
whose answers are known in advance. No DataMate data.

- `generate.py` writes `shop/orders.csv` (about 22,000 orders, June 2024 to 26 November 2026,
  gitignored) and `questions.jsonl` (committed), seeded so the bytes never change. Planted:
  - October 2026 falls against September, carried by branch Q1, mostly food;
  - 1–26 November falls against 1–26 October, led by GrabFood;
  - drinks grow every month, every December sells more;
  - three days sell five times a normal day;
  - discount and quantity move together.
- `questions.jsonl` has 16 questions: 10 need an analysis (2 per kind, one same-days comparison,
  one drill-down), 6 are controls that need none. Each has checks code grades.
- `score.py` grades an answer: the right kind of analysis (none for a control), and its checks.
- `spec.json` is the approved spec of the shop, so the build calls no model.

```bash
docker build -f sandbox/Dockerfile -t da-agent-sandbox:dev .
uv run python -m eval.analysis.run --output-dir eval/reports/analysis-build --build-only   # $0
uv run --env-file .env python -m eval.analysis.run --routes groq:openai/gpt-oss-20b \
    --output-dir eval/reports/analysis-1 --attempts 3        # real model calls: ask first
```

Results go to the output folder (`results.jsonl`, `metrics.json`); `eval/reports/` is gitignored.
The exit criteria per attempt: the right kind on at least 8 of 10 analysis questions, 7 of 10
correct, and at least 5 of 6 controls without an analysis.
