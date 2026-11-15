# Metric definitions, saved from an answer

**Status:** design approved pending review. Phase 1 of 2. An ADR (0036) records the decision when
it is built.

## Why

People who do not work with data ask in business words: "doanh thu", "đơn thành công", "khách
quay lại". Each word has several readings (cancelled orders in or out, line amounts or order
totals), and the agent guesses. Business-rule misuse is the largest failure group in
`final-regression` (19 of 57 not correct). Such a user cannot write a correction in the Memory
panel, because a correction is free text about columns and rules.

A metric definition gives a business word one reading that a person confirmed. Later questions use
it, and the answer says so, which makes answers both more correct and consistent: two questions
about "doanh thu" are computed the same way.

## Phases

1. **This design.** Save a definition from an answer the user trusts. Code builds the definition
   from the answer's SQL; the person names it and saves it. Later runs read it as a note, and code
   marks answers that used it.
2. **Later, separate design.** When the plan meets a business word with no definition, the agent
   asks with 2 to 3 choices built from real columns; a click resumes the run and saves the choice.
   It needs the clarify reply wired through the API and web (the graph already resumes with
   `Command(resume=reply)`, `graph.py`; the web today says "ask again"), a plan prompt change,
   and an eval run.

## Goals, non-goals, anti-goals

Goals (phase 1):
- Under an answered run's "How it was calculated", a "Save as a definition" action for a measure
  that code fully read.
- A definition holds: a name, optional other names, the table, the measure expression, and the
  row filters, plus a plain sentence written by the same code as ADR 0035. Period, grouping,
  ordering, limit, and HAVING are not part of a definition: "doanh thu" is the same in May and
  in June.
- Later runs read approved definitions in the notes document, before corrections.
- An answer shows "Used your definition of *Doanh thu*" only when code finds the definition's
  measure and filters in the evidence SQL.
- The Memory panel lists definitions: rename, edit other names, delete.

Non-goals:
- The agent asking which definition to use (phase 2).
- Verify forcing an answer to use a definition (approach B below; later, if evals show misses).
- A metric tool or a semantic layer that writes SQL (approach C).
- Editing a definition's SQL by hand. To change it, save it again from another answer.
- Definitions over joins, subqueries, CTEs, or window functions.
- Translating the UI (English for now).

Anti-goals (a failure even if it works):
- A definition that says something its SQL does not do. The sentence and the SQL come from one
  parse of one query.
- An answer claiming it used a definition when its SQL did not. The badge comes from code, never
  from the model.
- The agent creating or changing definitions on its own (ADR 0030: only people write memory).
- A stale definition, naming a column a rebuild removed, reaching the model.
- Changing eval behaviour: eval runs read no memory, so prompts there must stay byte-identical.

## Constraints

- ADR 0030: memory is written by people through the API, reaches the model as data inside the
  notes document, and is snapshotted per run (`memory_ids`) so a resume reads the same entries.
- ADR 0035: code reads SQL with sqlglot; labels show column names as in the uploaded file.
- Hidden (sensitive) columns never appear in a definition.
- No new service, no new dependency.

## Approaches

| Approach | Trade-off |
|---|---|
| **A. A `metric` memory entry the agent reads as a note; code marks answers that match (chosen)** | Small: one memory kind, one endpoint, a button, a note section. Grounded in SQL that once ran. The agent can still compute differently; the badge then does not show |
| B. A plus verify sending an answer back when the question names a metric and the SQL differs | Stronger, but matching a metric name in free Vietnamese text ("doanh số", "DT", "tiền bán") is fuzzy, so false repairs cost model calls. Kept for later |
| C. A semantic layer: a `compute_metric` tool, code writes the SQL | Most reliable, like dbt MetricFlow or Looker, but a new tool and query builder, less flexible, and against "the semantic layer holds data facts only" |

The user chose A on 2026-11-15, and phase 1 before phase 2. Prior art: Databricks Genie's SQL
expressions (measures and filters a person saves for a space), Snowflake Cortex Analyst's semantic
model metrics.

## Design

### Extracting a definition (backend, `da_agent.reports.metric`)

`metric_candidates(sql, labels, dialect) -> list[Candidate]` parses one evidence query. It returns
no candidates unless the query is a single `SELECT` over one table with no join, subquery, CTE,
window, or set operation, and the ADR 0035 explanation has no unexplained part.

Each aggregate in the select list whose explanation is complete becomes a candidate:
- `table`: the table name in the dataset.
- `measure`: the aggregate expression as SQL, normalised by sqlglot (`SUM("thanh_tien")`), with
  its `FILTER (WHERE …)` or one-branch `CASE` condition kept inside it.
- `filters`: the top-level `WHERE` conjuncts as SQL, minus date bounds (the same `date_bound`
  reader verify and 0035 use, so the period never becomes part of a definition).
- `sentence_facts`: the 0035 facts for that measure and those filters, for the web to phrase.

A candidate that names a hidden column is dropped.

### Storage

Migration `0012_metric_definitions.sql`:
- `kind` also allows `metric`.
- New nullable columns: `name text` (1 to 80 characters), `aliases text[]` (at most 10, each 1 to
  80), `definition jsonb` (`{table, measure, filters, facts}`). A check requires `name` and
  `definition` for `metric`.
- `text` holds the plain sentence, so existing list and audit code keep working.

`MemoryStore`:
- `save_metric(workspace, dataset, name, aliases, definition, text, author, source_run_id)`
  writes an `approved` entry. A live metric with the same name or alias, compared without
  accents and case, is superseded by the new one (the existing edit chain), so one name has one
  definition.
- Rename and alias edits use the existing edit path (a new entry superseding the old one).

### API

- `GET /v1/runs/{id}/metric-candidates`: per evidence query, its candidates. Computed on read, no
  SQL runs, like the explanation.
- `POST /v1/datasets/{id}/metrics` with `{run_id, query_index, measure_index, name, aliases}`.
  The server rebuilds the candidate from the stored run, never from a client-sent definition, so
  a client cannot save SQL that did not run. 422 when the candidate no longer exists or names a
  hidden column. Audit `memory.create` with `kind=metric`.
- `PATCH /v1/memory/{id}` accepts `name` and `aliases` for a metric (text and SQL stay read-only
  for metrics).
- `GET /v1/datasets/{id}/memory` returns metrics with `name`, `aliases`, `definition.facts`, and
  `stale`.
- The run explanation response gains `metrics_used: [{id, name}]` per evidence query (below).

### Reaching the model

`memory_documents` renders metrics first in the notes document, then corrections, aliases, and
examples:

```
## Metric 1: Doanh thu (also: doanh số, DT)
  When the question asks for Doanh thu, compute it on table orders as:
    SUM("thanh_tien")
  with these row filters, in addition to the question's period and grouping:
    "trang_thai" <> 'Hủy'
  (written by an@shop.vn)
```

The preface adds one sentence: "A metric is how the people who use this dataset compute that
word; use it whenever the question asks for it." With no metric, every document is byte-identical
to today's, so prompts without metrics, eval runs included, do not change.

**Stale metrics:** when the worker loads notes, a metric whose table or any column is not in the
current dataset is left out and logged. The API marks it `stale` in the panel ("The data changed;
save this definition again from a new answer").

### Marking answers

`metrics_used(sql, metrics, dialect)` in `da_agent.reports.metric`: a metric matches an evidence
query when the query reads the metric's table, one of its select-list aggregates normalises to the
metric's measure, and every metric filter appears among the query's top-level `WHERE` conjuncts or
inside that aggregate's condition. Extra filters in the query (a period, a product) do not break
the match. It checks only the metrics in the run's `memory_ids` snapshot, so an answer is never
marked with a definition saved after it ran. Computed on read in the explanation endpoint.

### Web

- In "How it was calculated", each measure with a candidate gets "Save as a definition". A small
  dialog shows the sentence ("Sum of *Thành tiền*, only rows where *Trạng thái* is not *Hủy*"),
  a name field, and an "Other names" field. Save calls the endpoint; the dialog says the
  definition applies from the next question.
- Above the explanation, when `metrics_used` is not empty: "Used your definition of *Doanh thu*",
  linking to it in the Memory panel.
- The Memory panel gets a "Definitions" section first: name, other names, sentence, author, and
  a stale warning; rename, edit other names, delete.
- Sentences come from the existing `explain.ts` phrasing, reused.

## Testing and measurement

- Unit tests first (TDD): candidate extraction (single table, CASE and FILTER conditions, date
  bounds left out, hidden column dropped, join or subquery gives none), matching (extra filters
  allowed, missing filter fails, different measure fails), note rendering, byte-identical
  documents without metrics, store supersede by name and alias, stale detection, and the API
  (save from a run, 422 paths, workspace isolation, audit).
- Web unit tests for the dialog state and the badge.
- **Effect eval, small and local to the private competition folder:** dev questions whose failure
  was business-rule misuse, run with and without a few definitions saved from correct answers.
  Definitions come from correct runs, never from `answers.jsonl` or holdout shops. Roughly 20
  questions; cost asked before running and reported after. The standard eval reads no memory, so
  `regression` needs no rerun; a test asserts the eval prompt is unchanged.
- Browser check of the save, badge, and panel flow.

## Left to the builder

- Exact sqlglot normalisation for comparing measures (quoted vs unquoted identifiers, `count(*)`
  vs `count(1)`); test the cases found in local eval SQL.
- Dialog and panel layout within the existing shadcn components.
- Whether `metric-candidates` merges into the explanation response instead of a separate route,
  if that is simpler for the web.
