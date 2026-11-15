# 0036: Metric definitions are saved from an answer's SQL and read as approved memory

**Status:** Accepted, 2026-11-15. Extends [0030](0030-conversation-and-dataset-memory.md) and
[0035](0035-plain-explanation-from-sql.md). Design:
[2026-11-15-metric-definitions.md](../design/2026-11-15-metric-definitions.md).

**Context:** People who do not work with data ask in business words: "doanh thu", "đơn thành
công". Each word has several readings, and the agent guesses; business-rule misuse is the largest
failure group in `final-regression` (19 of 57 not correct). A correction in memory is free text
about columns and rules, which such a person cannot write. Databricks Genie (SQL expressions) and
Snowflake Cortex Analyst (semantic model metrics) let people save a measure and its filters once.

**Decision:**
- A **metric** is a `dataset_memories` entry of kind `metric` with `name`, up to ten `aliases`,
  and `definition`: the table, the measure, and the row filters as canonical SQL, plus the 0035
  facts to show it in words. Period, grouping, order, limit, and HAVING are not part of it.
- **Saved from an answer, read by code.** `POST /v1/datasets/{id}/metrics` names a finished run,
  one of its evidence queries, and a measure. The server reads the definition from the stored SQL
  with sqlglot; the body never carries SQL. Only a single-table query that 0035 fully explains
  gives candidates; a condition inside the aggregate (`FILTER`, a one-branch `CASE`) becomes a
  filter, so `COUNT(*) WHERE s` and `COUNT(CASE WHEN s THEN 1 END)` are one metric.
- **One name, one definition.** Saving a name or alias that a live metric of the dataset already
  has, compared without accents or case, supersedes it (the 0030 edit chain), under a per-dataset
  advisory lock. A metric is renamed, never edited; to change what it computes, save it again.
- **Read first.** Metrics open the notes document, before corrections, as "when the question asks
  for X, compute it on table T as M, with these row filters". With no metric, the documents are
  byte-identical to before.
- **Stale metrics are left out.** When the dataset no longer has the table or a column a metric
  reads, the worker skips it and logs it, and the API marks it `stale` for the Memory panel.
- **The badge comes from code.** "Used your definition of X" shows only when an evidence query
  computes the metric's measure on its table with every one of its filters, in `WHERE` or inside
  the aggregate. Extra filters such as a period are fine. Only metrics in the run's `memory_ids`
  snapshot are checked, so a run older than a metric is never credited with it.

**Consequences:**
- A person without SQL fixes a business word once, and later answers say when they followed it.
- The model can still compute differently; then the badge does not show. Verify does not enforce
  definitions yet.
- Eval runs read no memory, so the standard eval is unchanged. Effect is measured separately on
  dev questions with definitions saved from correct runs.
- One migration (`0012`), one module (`reports/metric.py`), two routes, and web changes.

**Rejected:**
- Verify sending an answer back when the question names a metric and the SQL differs: matching a
  name in free Vietnamese text is fuzzy, so false repairs would cost model calls. Revisit if the
  effect eval shows definitions ignored.
- A `compute_metric` tool where code writes the SQL (dbt MetricFlow, Looker): reliable but a new
  tool and query builder, less flexible, and against "the semantic layer holds data facts only".
- The client sending the definition: a client could save SQL that never ran.
- Keeping a `FILTER`/`CASE` condition inside the measure: two spellings of one metric would not
  match each other.
- The agent asking which definition to use: phase 2, which needs the clarify reply wired through
  the API and web.
