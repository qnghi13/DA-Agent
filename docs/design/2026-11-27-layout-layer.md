# Layout layer: from a sheet's grid to tidy tables

**Status:** built and measured on branch `fix/ingest-multiline-cells` (2026-11-27): dev 48/49
targets, held-out 38/47 ([layout eval](../eval/layout.md), [ADR 0052](../adr/0052-layout-layer.md)).
Plan in [2026-11-27-layout-layer-plan.md](2026-11-27-layout-layer-plan.md). What changed while
building is under "Decisions made while building".

## Decided by the user (2026-11-27)

- Split the 14 private shops evenly into dev and held-out, balanced by layout and question count.
- A stronger model may read layouts.
- Evals first. No approval-screen preview yet.
- Hand-written layout targets for the dev shops, so the layer can be scored.

The split (Claude's call, balanced as asked): each layout kind below is on both sides, except
side-by-side tables and paired rows, which occur in one shop each.

| | Shops | Questions | Layouts |
|---|---|---|---|
| Dev | 004, 006, 012, 015, 021, 022, 024 | 261 | side-by-side tables, multi-row headers, numeric headers, context in titles and sheet names, section rows, `key: value` records, drifting headers, SQLite |
| Held-out | 005, 010, 014, 017, 019, 020, 023 | 239 | multi-row headers, numeric headers, paired rows, section rows, context in sheet names, calendar cells, repeated sheets, a clean shop |

Every shop was looked at once for the analysis of 2026-11-27, so the held-out side is weaker than
an unseen one. From now on its files are opened only by the final scored run.

## Why

Raw ingestion assumes one sheet is one table under one header row
([architecture: Ingestion](../architecture.md#ingestion)). A new private set of 14 shops (not in
any commit) breaks that assumption in 8 of them before the model sees anything. The code-only raw
step produced wrong or lossy tables, and in one shop it crashed the whole upload. Layouts seen,
described generically:

| Layout | What raw ingestion does today |
|---|---|
| Header in two or three rows (a group label such as a term or "dates" above its sub-columns) | Sub-columns become `TX1_2`, `column_7`: the group meaning is lost |
| Header cells that are numbers (day 1–31 of a month) | The header is skipped and the first record becomes the header |
| Context outside the table: title rows ("class X, subject Y, term 1"), the sheet name ("T2.2025", needed to read dates written as `T1/25`) | Dropped, so rows no longer say which subject, class, or month they belong to |
| Section rows inside the table (a department name, "payment period 01–07") | Left as junk rows, or taken as the header |
| Two tables side by side in one sheet, split by an empty column | One wide table |
| Records written as `key: value` lines down one column, split by a separator line | One text column with every line as a row |
| A calendar grid whose cells hold several `key: value \| key: value` records, one per line | Crashed the upload (fixed, see below) |
| Day columns × paired rows (time in / time out per person per day) | Wide table with meaningless columns |
| One logical table over many sheets or files whose headers drift (column order, case, a new column, two sheet templates in one workbook) | Several tables, up to 35 in one shop |
| Repeated sheets and files (`T3 (2)`, `file (1).xlsx`, an export overlapping two months) | Partly handled by the existing overlap rules |

Value-level mess (money as `13tr2`, dates as `T2/1`, names with and without diacritics) and
domain rules are separate projects (C, D, E in the analysis of 2026-11-27). They need tidy tables
first, so this layer comes first.

### Crash fix (done, 2026-11-27)

Work files are CSV with quoted cells. DuckDB guessed the line ending, and a line break inside a
cell made it pick the wrong one and reject the file, which failed the whole ingest. The work files
now end rows with `\n`, DuckDB is told so, and a table that still fails to load is dropped with a
warning while the other tables load. Two tests in `tests/unit/test_ingestion_raw.py`.

## Prior art

| Work | What it does | What we take |
|---|---|---|
| **Auto-Tables** (Li et al., VLDB 2023, Microsoft) | Predicts a pipeline of 8 restructuring operators (`stack`, `wide-to-long`, `transpose`, `pivot`, `explode`, `ffill`, `subtitles`, plus parameters) for a table that is already cut out. Training data comes from applying inverse operators to relational tables. An input-output reranker picks among candidates. 57% top-1 and 75% top-3 on 244 real cases; GPT-3.5 few-shot 20% top-1. 26 of 244 cases needed more than one step, 21 of those from Excel. | The operator set. Proposing several candidates and **ranking them by checking the output** rather than trusting one guess. |
| **SpreadsheetLLM** (Dong et al., EMNLP 2024, Microsoft) | Encodes a sheet for an LLM: keeps rows and columns within 4 cells of "structural anchors" (rows that differ from their neighbours), an inverted index of value → cell ranges, and groups of same-format numbers. Cuts tokens about 96%. Table detection F1 for GPT-4 in-context rose from 0.15 to 0.41; fine-tuned models reach about 0.72–0.76. | How to show a large sheet to the model cheaply. Detection by a prompted model alone is weak, so code should find regions and the model should only confirm and label them. |
| **unpivotr / tidyxl** (R, Garmonsway) | Reads cells with positions, then `behead(direction, name)` strips one header layer at a time ("up-left": a group label above and left of its columns). | Multi-row headers as a sequence of directional header layers, not one joined string. |
| **Koci et al.** (TU Dresden, 2016–2019); **Strudel** (Jiang et al., EDBT 2021) | Classify each cell or line as header, data, metadata, attribute, derived (totals), or note, then build rectangular regions and a graph of them. | The cell roles we report, and "derived" rows found by arithmetic, which we already do (`sums_above`). |
| **FlashRelate** (Barowy et al., PLDI 2015) | Synthesizes an extraction program in a spatial pattern language from user examples. | Declarative spatial rules, without asking a non-technical user for examples. |
| **SpreadsheetAgent** (Ren et al., ACL 2026) | An LLM writes a YAML structure sketch of each sheet (ranges, nested header trees), and a verifier agent checks it and asks for revisions. With `gpt-oss-20b` answering, a large extractor model raised accuracy from 25.3 to 32.2; a 30B extractor gave 25.7, about the same as none. | A structured description checked in a loop. **Structure reading pays for a stronger model even when questions use a small one**, and ingestion runs once per dataset. |
| **Role-labelled chunks** (Smoleń, arXiv 2609.20732, 2026) | Small graph networks label every cell with one of 13 roles (header depths, aggregation, metadata) for spreadsheet QA; better than SheetCompressor and Unstructured on 480 questions. | Header depth and totals are the roles that matter most. A trained classifier is out of scope for now, but its role list is a good target. |
| Unstructured, Docling (`xlsx`) | Split a sheet into tables by connected groups of filled cells. | The first, code-only region cut. |

Two lessons hold across these. First, prompted models are poor at finding table boundaries in a
raw grid, and code or trained models do better. Second, the reliable systems describe structure
in a small fixed language and check the result, rather than letting a model write free code.
Both fit ADR 0006 (the spec is declarative data) and ADR 0051 (free Python was misused by our
target model).

## Proposal

A new **layout stage** between raw reading and the `DatasetSpec`. It turns each sheet into one or
more tidy tables plus context columns. The existing spec stage then types and cleans columns as
today.

```
read cells (sandbox) ─> code: regions, row roles, sheet families, coverage
      │                          │
      │          flagged families only (grid preview + code's findings)
      │                          v
      │                 model proposes LayoutSpec, up to k candidates (Worker)
      │                          │
      └──────> code applies each candidate, scores coverage and column consistency (sandbox)
                                 │ best candidate, or back to the model (≤ 3 rounds)
                                 v
                      tidy raw tables ─> existing DatasetSpec stages
```

### 1. Code first

- **Cells, not rows:** keep each value's row and column, and its type from the file (number, date,
  text). Ignore formulas and macros as today.
- **Regions:** connected groups of filled cells, split by fully empty rows or columns.
- **Row roles:** title (few cells, above the header), header candidates (text-heavy, or a run
  such as 1…31), section (one filled cell inside the data, repeated), total (`sums_above`, or a
  label such as "total" next to one number), footer (page markers), data.
- **Sheet families:** sheets and files whose header candidates match, ignoring case, order, and
  accents, are one family. What varies between them, such as the sheet name or a title cell, is
  a context value candidate.
- **Fast path:** a family with one header row, homogeneous columns, and full coverage needs no
  model call. That keeps the 24 dev shops on today's path, and any change there is a regression.

### 2. The model proposes a `LayoutSpec` for flagged families

The model sees a compact grid preview, with anchor rows and their neighbours, repeated rows
collapsed, and cell addresses, plus code's findings. It answers with data from a fixed set of
operators:

| Operator | Parameters | Covers |
|---|---|---|
| `region` | "region n" from code's findings | side-by-side tables, a table under a title block |
| `header` | number of header rows; per layer a direction (`up`, `up-left`) | two- and three-row headers |
| `context` | name, and a source: sheet name, file name, a cell address, or the cell right of a label | class, subject, term, month from titles and sheet names |
| `sections` | which rows, and the new column's name | department rows, payment-period rows |
| `exclude` | row roles to drop (title, total, footer), with a reason | totals and page footers, kept in `_excluded` as today |
| `unpivot` (Auto-Tables `stack`) | columns from–to, names for key and value | day 1–31 columns |
| `split_groups` (Auto-Tables `wide-to-long`) | the header layer whose labels name the groups, and the new column's name | two terms of the same score columns side by side |
| `pair_rows` | the column that alternates (in / out) and its values | time-in and time-out rows |
| `record_blocks` | key/value separator, record separator or "a key repeats" | `key: value` lines down one column |
| `split_cell` (Auto-Tables `explode`) | line separator, field separator, key/value separator | several records packed in one calendar cell |
| (code, no parameter) | sheets of one family are stacked by column name after the case and accent rules | one table over many sheets or files with drifting headers |

`transpose` is left out: no shop needs it (YAGNI).

The operators are declarative and code interprets them. There is no regex the model writes (as in
`RowCondition` today), no code, and limits on counts and sizes.

### 3. Code applies and judges

- **Coverage:** every filled cell must end up in exactly one place: a value in an output row, a
  header or context value that was used, or an excluded row with a reason. Coverage under a
  threshold names the cells left over and goes back to the model. This is the layout
  counterpart of today's parse rate.
- **Consistency:** in a tidy table each column holds one kind of value. A share of mixed kinds per
  output column is the cheap stand-in for Auto-Tables' learned reranker.
- **Ranking:** with k candidates, code keeps the best by coverage and then consistency. A tie
  goes to the user's approval screen with both previews.
- **Provenance:** build relies on `_row` being unique and ordered within a source (ordering,
  fill-down, overlap, quarantine probes). In a table made by a layout, `_row` is therefore the
  record's ordinal within its source, in reading order, and a new `_cell` column holds the sheet
  range the record came from, such as `A5:J5` or `F12`. Fast-path tables keep `_row` as the
  sheet row and have no `_cell`.

### 4. User approval

Later: the approval screen also shows, per family, the original grid next to the tidy rows.
Evals auto-approve, as today.

### 5. Model for ingestion

Ingestion runs once per dataset and version, and reading structure benefits from a stronger
model (SpreadsheetAgent result above). Layout proposals use their own routes,
`DA_AGENT_INGEST_ROUTES` (the question routes when unset); the first candidate is Groq
`openai/gpt-oss-120b`, with its price checked on Groq's page before the first run. Spend limits
and cost records cover it like any call.

## Decisions made while building

- **What sends a family to the model.** A title row alone does not: in the 24 older dev shops,
  titles are constant report metadata ("Report period: all"). A title flags the family only when
  its values differ across the family's sheets. Two more flags: `header_moved` (code's header row
  is not the one the fast path takes, or the fast path finds none) and `sheet_context` (sheet
  names that name things, not months). With these rules none of the 24 older shops is flagged, so
  they keep the fast path and make no model call.
- **Family ids are content hashes** (`f` and 6 hex digits of the header shape), not `f1, f2`, so a
  stored layout still finds its family when files are added.
- **The layout is stored in the approved spec** (`DatasetSpec.layout`). The product ingests again
  on approval and on added files, and both pass the stored layout. Only the layout stage sets it.
- **Operators.** A family may have one entry per region (side-by-side tables). A second step,
  `then`, runs on the first one's output (paired rows, then unpivot of days). `split_cell` names
  the column that holds each cell's header. `record_blocks` shares the lines before a group's first
  record with every record of the group (an invoice with several service lines). `unpivot` to a
  last column a sheet lacks runs through the like-named columns (30-day months).
- **Coverage** counts a cell taken twice against the layout: share = (used + excluded − twice) /
  filled, over the whole sheet and every entry of the family.
- **Rows a layout excludes** (titles, totals, page footers, repeated headers) are dropped at raw
  and counted in coverage; they are not in `_excluded`, which keeps the rows build excludes.
- **Hand-written layouts** reach 48 of 49 dev targets (baseline 30), at coverage 1.0 in every
  family: the operators are enough for the dev shops. The miss is a statement's per-period
  summary line with no total label.
- **Coverage alone was blind to shape** in the first model run, so code also checks the output's
  shape (day columns left wide, repeated sub-columns, `key: value` columns, a context cell that
  never changes beside one that does), counts it in the score, and says it to the model.

## How to measure it

There are no answers for the private set, so question accuracy cannot be scored there. Proposal:

- **Layout targets:** for each flagged family in a dev split of the private shops, a small
  hand-written target: output tables, columns, row count, and three sample rows. Stored next to
  the data, never in a commit. Metric: families whose output matches.
- **Coverage and fast-path share** on every shop.
- **No regression** on the 24 dev shops: same tables and parse rates, and no model call where
  there was none.
- **Generality:** some private shops kept aside and looked at only after the design is frozen.
- **Unit tests** on small synthetic sheets for each operator. CI never sees the private data.

## Out of scope here

Value parsing (money, dates with sheet-year context, units), entity resolution and directories,
derived semantic views, and the agent's tools. Each needs tidy tables first and gets its own
design.

## Layout targets

One JSON line per target, kept under `private_test/layout_targets/` (ignored in `.gitignore`) and
never committed:

```json
{"dataset": "shop_a", "source": "Book.xlsx#Sheet 3", "rows": 86,
 "samples": [["S043", "An Bình", "TERM I", "4", "7", "6.8", "Math", "7A2"]]}
```

- `rows`: how many output rows must come from that source.
- `samples`: each is a set of cell texts that must all appear in **one** output row from that
  source. Matching is by value, after trimming, so the model's column names do not matter.
- A target passes when the row count matches and every sample is found. The score is passed
  targets over all targets, per shop and in total.
- The scorer reads the raw tables (`raw.duckdb`), because layout is a raw-stage property. That
  scores today's code-only ingest for free, which is the baseline.
