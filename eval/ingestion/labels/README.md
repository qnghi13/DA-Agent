# Labels of rows that are not records

Ingestion quality labels, separate from the DataMate question answers. They list subtotal, total,
and note rows by `_source` (file, or file#sheet) and `_row` (1-based row in that sheet). They hold no
cell values.

| File | Covers | Rows |
|---|---|---|
| `datamate_dev.jsonl` | The 24 DataMate dev shops. No holdout shop was read | 2,032 in 5 shops |
| synthetic | Written by `python -m eval.ingestion.synthetic`, not stored here | 120 in 4 of 5 datasets |

Each line: `{"dataset": "biz_104", "source": "DonHang_ChiTiet.xlsx#Sheet1", "row": 139, "kind":
"daily_subtotal"}`. Kinds: `daily_subtotal`, `monthly_total`, `note`, `subtotal`, `total`.

## How `datamate_dev.jsonl` was made (2026-11-06)

1. For every raw table of every dev shop, list row shapes (sets of filled columns) that cover under
   5% of the table and are sparser than the common shape, with examples. Six groups appeared.
2. Independently, scan every raw cell for cells that start with a total or note word (`tổng`,
   `cộng`, `total`, `subtotal`, `grand`, `sum`, `ghi chú`, `note`, `lưu ý`, `chú thích`). It found
   the same six groups, plus customer names with the surname "Tong", which are records.
3. Each group was reviewed by hand and labelled in full.

Rows that are not records but look like records, with every column filled, would be missed by step 1
and by step 2 unless they carry such a word. None was seen.

## Scoring

```bash
uv run python -m eval.ingestion.exclusions --labels eval/ingestion/labels/datamate_dev.jsonl \
    --datasets eval/reports/prepare-v2/datasets
```

Report precision (excluded rows that are labelled) and recall (labelled rows that are excluded).
Excluding a real record is worse than keeping a subtotal, so precision matters more.
