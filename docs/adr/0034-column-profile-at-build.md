# 0034. The column profile is computed during the build and kept in the dataset summary

**Status:** Accepted (2026-11-14). Extends [0006](0006-dataset-spec.md) and
[0019](0019-dataset-approval.md). Design:
[2026-11-14-data-overview.md](../design/2026-11-14-data-overview.md).

**Context:** Analysts first want to know what each column holds: type, missing share, cells that
failed to parse, distinct values, ranges, and frequent values. The build already runs in the
sandbox with the clean table open, and already counts filled and parsed cells. The API reads
dataset summaries from Postgres and never opens DuckDB files. The agent's prompt is built from the
build report's `min_value`, `max_value`, and `values`, so changing those fields would change the
agent and need an eval run.

**Decision:**
- The build adds `distinct`, `number_min`, `number_max`, `number_mean`, and `top_values` (five
  most frequent text or boolean values, cut to 60 characters, with counts) to each column of the
  build report. Existing fields keep their meaning; the agent's prompt does not read the new ones.
- The worker copies them into each table's `profile` in the dataset summary. The web shows it under
  each table in the review before approval and in "About this data".
- A profile on demand (`SUMMARIZE` in a `profile` job) and a profile computed by the API were
  rejected: the first starts a sandbox per view for data that does not change after a build, the
  second makes the API open dataset files.

**Consequences:**
- No model call and no change to agent behaviour, so no eval run.
- Datasets built before this have no profile until uploaded again: a ready dataset cannot be
  rebuilt today.
- The summary in Postgres now holds up to five values per visible text column. Hidden columns are
  not in the clean table, so they are never profiled.
