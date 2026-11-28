# Layout layer evaluation

Aggregates only. The private shops, their targets, and raw runs are not in the repository. Design:
[layout layer](../design/2026-11-27-layout-layer.md).

## Setup

- **Data:** 14 private shops with layouts that are not "one header row, then records". Split 7/7
  into dev and held-out (`private_test/splits.json`, not committed).
- **Targets:** one hand-written target per sheet family of each dev shop, clean ones included:
  the expected row count from that source and three sample rows (first, middle, last record),
  each a set of cell texts that must sit in one output row. Context values (class, subject,
  month, sheet name) are part of the samples when the tidy row needs them. Scorer:
  `eval/ingestion/layout_targets.py`, on the raw tables (`raw.duckdb`).
- **Raw-stage rule:** a family the layer leaves on the fast path (one header row, then records)
  keeps a trailing total row in its raw table, as today, because build excludes it later and the
  exclusion eval scores that. A family the layer reshapes drops totals, page footers, and
  repeated headers itself.

## Results

| Run | Date | Targets passed | Cost |
|---|---|---|---|
| Code-only raw ingest (`eval/ingestion/raw_only.py`), the baseline | 2026-11-27 | 30 / 49 | $0 |
| Hand-written layouts (`--layouts`), the ceiling of the operators | 2026-11-27 | 48 / 49 | $0 |
| Groq `gpt-oss-120b` proposes, run 1 (coverage only) | 2026-11-27 | 41 / 49 | $0.018 |
| run 2: code checks the output's shape and says what is not tidy | 2026-11-27 | 45 / 49 | $0.019 |
| run 3: every flagged family counts in the score; group labels always spread | 2026-11-27 | 47 / 49 | $0.016 |
| run 4: repeated keys inside a record reported | 2026-11-27 | 45 / 49 | $0.018 |
| run 5: groups on the column-name layer refused; keys found listed | 2026-11-27 | 44 / 49 | $0.017 |
| runs 6 and 7: page footers always dropped; "Label \| value" titles in the prompt | 2026-11-27 | 48 / 49, 48 / 49 | $0.018, $0.017 |

Per shop (baseline): 4/7, 6/8, 6/7, 0/8, 5/7, 5/7, 4/5. What fails: context outside the table
(class, subject, term, month, sheet name) is lost, two- and three-row headers, day columns,
side-by-side tables, `key: value` lines, title rows taken as the header, and page totals and
repeated headers inside the table.

Hand-written layouts reach every target but one, with coverage 1.0 in every family: the operators
can express the dev layouts. The miss is a payment statement's per-period summary line, which has
no total label and does not sum the rows above it. In the 24 older dev shops no family is flagged,
so their raw tables are unchanged (checked against `main`: same tables, columns, and row counts).

Runs with the model (`raw_only --layout-routes`, two candidates, up to three rounds, 14 to 17
calls and about 70,000 tokens for the seven shops): dev spend $0.123 over seven runs. Run-to-run
variance is large (runs 3 to 5 moved by three targets with small code changes), so the last code
was measured twice. Coverage alone could not tell a tidy table from a wide one: the label cell
taken as context, day columns left wide, and a failed reshape all scored full coverage. The fixes
that mattered were code checks on the output's shape, fed back to the model and counted in the
score, and operators that refuse or report what they cannot do instead of passing rows through.

## Held-out (run once, 2026-11-27)

Targets for the seven held-out shops were written before any held-out run, with the same rules
as dev: 47 targets. Nothing was changed after this run.

| Run | Targets passed | Cost |
|---|---|---|
| Code-only raw ingest | 33 / 47 | $0 |
| Groq `gpt-oss-120b` proposes (code of dev runs 6 and 7) | 38 / 47 | $0.026 (16 calls, 108,000 tokens) |

The gap from dev (48 of 49) is the generality the held-out side was kept for. The nine misses, by
cause:

| Layout | What went wrong | Where |
|---|---|---|
| Time in and out rows per person and month, days as columns | Code split the sheet into five regions, one per department: a department row fills only column A, and the "Out" row above it leaves column A blank, so the two do not touch. The blocks are not joined again because code takes a data row of times as each lower block's header (times such as `07:10` read as text). The model then used neither `pair_rows` nor `unpivot` | region finding, row roles, model |
| A calendar whose cells hold several `key: value` lines (two sheets) | The model used `record_blocks`, not `split_cell`; the feedback named no keys, because it reads only the first line of a record | feedback |
| Purchase slips (two sheets) | Five and thirteen records taken as non-records by the row roles | row roles |
| A list of abbreviations, most rows a single cell | Single-cell records taken as section rows | row roles |
| A monthly total written as `128tr450` (two sheets) | Not read as a number, so not a total | number reading |
| A daily sales book | The layer dropped a monthly total the target keeps at raw | the target's rule |

`pair_rows` and `split_cell`, the two operators no dev shop needed, were only exercised by unit
tests before this run, and neither was used successfully.

## Synthetic layouts after held-out (2026-11-28)

Two held-out misses were fixed on a synthetic upload made for them (`eval/ingestion/layout_synthetic.py`,
seeded, committed): a timesheet with In and Out rows per person and month, department rows, and
days as columns; and a monthly calendar whose cells hold several `key: value` records. Held-out was
not run again: 38 of 47 stands.

| Run | Targets passed | Cost |
|---|---|---|
| Code-only raw ingest | 0 / 3 | $0 |
| Hand-written layouts | 3 / 3 | $0 |
| `gpt-oss-120b`, first run | 2 / 3 | $0.005 |
| after the fixes below | 3 / 3 | $0.003 |
| two more cases added (6 targets): code only | 1 / 6 | $0 |
| `gpt-oss-120b` after the second fixes | 6 / 6 | $0.005 |

What the synthetic runs found, all in code:
- A data row of clock times read as a header, so blocks split at department rows were not joined
  (the earlier diagnosis, blocks of different widths, was wrong: they were the same width).
- `check_layout` split a header name such as `In/Out` on its slash and dropped every `pair_rows`
  entry the model wrote. The model had proposed the right operator three times.
- After `pair_rows`, the shape check advised `split_groups` for `1 / In, 1 / Out, ...` instead of
  unpivoting the days; and a section filled down was split into an In and an Out column.
- A record key the lines lack was answered with no keys, because only the first cell was read.

Dev after these fixes: 48 / 49 ($0.017), and no older shop is flagged.

Second fixes, for the two other held-out misses, on two more synthetic sheets (a two-column list
of abbreviations where many rows fill one cell, and a monthly sales book ending in `128tr450`):
- A one-cell row is a section only under a header of three or more columns. In a two-column table
  it is a record with one value missing.
- Compact millions (`13tr2`, `128tr450`) are numbers in row roles, and a table's last row that
  holds only a few numbers under blank leading cells is its total, even when the sum cannot be
  checked.

Dev after these fixes: 48 / 49 ($0.017), and no older shop is flagged.
