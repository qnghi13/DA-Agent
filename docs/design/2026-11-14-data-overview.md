# Data overview: a profile of every column

**Status:** implemented. **ADR:** 0034.

## Why

The first thing an analyst asks of unfamiliar data is what each column holds: its type, how much is
missing, whether some cells failed to parse, which values occur, and the range of dates and
numbers. "About this data" shows only tables, row counts, and the build's notes. Without a profile,
a person cannot see why an answer may be off (5% of quantities unreadable, `completed` and
`Completed` both present) or what to write in Memory.

## Goals and non-goals

- Per column: type, filled share, parsed share with failed samples, distinct values, range
  (dates and numbers), mean (numbers), and the most frequent values with counts (text, boolean).
- Shown in "About this data" and in the review before approval, which share one component.
- No model call, no change to what the agent sees (its prompt reads `min_value`/`max_value` and
  `values`, which stay as they are), so no eval run is needed.
- Non-goals: histograms, correlations, profiling hidden (sensitive) columns, profiling raw uploads.

## Approaches

| Approach | Trade-off |
|---|---|
| **A. Compute during the build, store in the dataset summary (chosen)** | The build already runs in the sandbox with the clean table open and counts filled and parsed cells; a few aggregates more per column. The API reads the summary from Postgres as today. Datasets built before this show no profile until rebuilt |
| B. A `profile` job on demand (DuckDB `SUMMARIZE`) | Works for old datasets, but a sandbox start per view and a new job kind for data that never changes after a build |
| C. Compute in the API | The API would open DuckDB files, which it never does |

## Design

1. `ColumnBuild` gains `distinct`, `number_min`, `number_max`, `number_mean` (integer, number,
   money, quantity) and `top_values` (text and boolean: the five most frequent, value cut to 60
   characters, with row counts). Date ranges keep using `min_value`/`max_value`.
2. The worker's dataset summary gives each table a `profile`: one entry per visible column with
   `name, type, filled, parsed, distinct, min, max, mean, top, failed_samples`. Hidden columns are
   not in the clean table, so they have no entry.
3. The web summary shows a "Columns" list under each table: name and type, filled %, a parse
   warning with samples when parsing failed, distinct count, the range or top values.
