# How it was calculated: a plain explanation of the answer's queries

**Status:** approved 2026-11-15. **ADR:** 0035.

## Why

The project serves people who do not read SQL first. Today such a person sees a number and a SQL
tab they cannot check: whether cancelled orders count, which month was summed, or which column was
added up stays hidden. A DA reads the SQL; everyone else needs the same facts in words. This is also
the base for metric definitions later ("Used your definition of Revenue").

## Goals and non-goals

- Under each answer, a short "How it was calculated" block: tables and joins, what was computed,
  the filters, the period, the grouping, and the top-N.
- Every sentence comes from the SQL that produced the answer, so it cannot say something the query
  did not do. When a part cannot be put in words (subquery, window, set operation, an unknown
  function), the block says so and points to the SQL tab. It never presents a partial reading as
  the whole.
- Column names appear as in the uploaded file (`Thành tiền`, not `thanh_tien`).
- English, like the rest of the UI. The API returns structured facts and the web writes the
  sentences, so translating the UI later does not touch the backend.
- Non-goals: a model writing or polishing the text, explaining exploratory queries, explaining
  edited SQL, changing the agent's prompt or graph.

## Approaches

| Approach | Trade-off |
|---|---|
| **A. Code reads the SQL with sqlglot (chosen)** | Free, deterministic, always matches the SQL. Phrasing is plain and covers common shapes only; the rest is flagged |
| B. The model writes an explanation with the answer | Fluent, but costs tokens, changes the prompt (eval run), and can describe what the SQL did not do |
| C. Code extracts facts, a model phrases them | Fluent and grounded, but still a model call per answer; kept for later if A reads too stiffly |

## Design

1. `da_agent.reports.explain.explain_sql(sql, labels, dialect) -> Explanation` parses one query:
   `tables`, `joins` (table, kind, column pairs), `measures` (aggregate, column, distinct,
   condition from `FILTER (WHERE …)` or a one-branch `CASE`; `+ - * /` between measures),
   `shown` (plain selected columns), `filters` (equals, not equals, in, not in, compare, between,
   contains, starts with, is blank, is not blank; case-insensitive when wrapped in `lower`/`upper`),
   `periods` (from verify's date-bound reader), `groups` (column, with a unit for `date_trunc`,
   `strftime`, `year`, `month`), `having`, `order` and `limit`, and `unexplained` (names of the
   parts left out). `complete` is true only when `unexplained` is empty.
2. Which queries: the answer's evidence queries (the ones whose results hold its numbers, or the
   last query when the answer has no numbers), the same rule `verify` uses.
3. Labels come from the dataset spec: `(table, clean name) -> source`. A column whose source equals
   its clean name shows as is.
4. `GET /v1/runs/{id}` adds `explanations: [{query_index, …}]` for a succeeded run with an answer.
   It is computed on read: parsing only, no SQL runs, bounded by the stored queries. Old runs get
   it too, with no migration.
5. The web shows the block under the answer, open by default, one sentence per part, and a note
   with a link to the SQL tab when `complete` is false.

## Measured

Coverage is measured offline on the agent's SQL in local eval reports ($0). Only aggregate shares go
into the docs.
