# Metric Definitions (phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a person save a metric definition from an answer's SQL, have later runs read it as
an approved note, and mark answers whose SQL used it.

**Architecture:** A pure module `da_agent.reports.metric` reads candidates from a query and matches
definitions against queries, with sqlglot. Definitions are `dataset_memories` entries of kind
`metric` (ADR 0030 store, snapshot, audit). The agent sees them first in the notes document; the
API computes candidates and matches on read; the web adds a save dialog, a badge, and a panel
section.

**Tech Stack:** Python 3.12, sqlglot, psycopg, FastAPI, Pydantic; React + TypeScript, vitest,
shadcn/ui.

**Spec:** [2026-11-15-metric-definitions.md](2026-11-15-metric-definitions.md)

## Global Constraints

- No new dependency, no new service.
- Only people write memory; a metric is `approved` when saved and never written by the graph.
- The server rebuilds a definition from the stored run; it never accepts definition SQL from the
  client.
- A definition holds table, measure, and non-date row filters only: no period, grouping, order,
  limit, or HAVING.
- Candidates only from a single-table `SELECT` with no join, subquery, CTE, window, or set
  operation, and an explanation with no unexplained part.
- With no metric, `memory_documents` output is byte-identical to before. Eval runs read no memory.
- Names 1–80 characters; at most 10 aliases, each 1–80; names and aliases compared without accents
  and case.
- The "Used your definition" badge comes from code matching, only for metrics in the run's
  `memory_ids`.
- UI copy in English. Code and docs in English; the learning note in Vietnamese.
- Tests first (`tdd` skill). Postgres tests run with `uv run --env-file .env pytest`.

## Review Focus

- A query that uses the metric with an extra period or product filter: still marked as using it
  (Task 1 test `test_extra_filters_still_match`).
- The metric's filter written in another order or with other quoting (`"trang_thai" <> 'Hủy'` vs
  `trang_thai != 'Hủy'`): still matches (Task 1 test `test_equivalent_spelling_matches`).
- A dataset rebuilt without a column the metric names: the metric never reaches the model and the
  panel says stale (Task 3 `test_stale_metric_is_left_out`, Task 4 `test_memory_list_marks_stale`).
- Saving "Doanh Thu" when "doanh thu" exists: replaces it, does not add a second (Task 2
  `test_same_name_without_accents_supersedes`).
- A run created before a metric was saved: never shows the badge (Task 4
  `test_badge_only_for_metrics_in_snapshot`).

---

### Task 1: Candidates and matching in `da_agent.reports.metric`

**Files:**
- Create: `src/da_agent/reports/metric.py`
- Test: `tests/unit/test_metric.py`

**Interfaces:**
- Consumes: `explain_sql`, `Labels` (`reports/explain.py`); `date_bound` (`agent/verify.py`).
- Produces:
  - `@dataclass(frozen=True) Definition(table: str, measure: str, filters: list[str], facts: dict)`
    with `to_json() -> dict` and `Definition.from_json(dict) -> Definition`. `measure` and each
    filter are canonical SQL strings (see Step 3). `facts` is `{"measure": <0035 aggregate fact>,
    "filters": [<0035 filter facts>]}`, labelled with the file's column names.
  - `metric_candidates(sql: str, labels: Labels, visible: dict[str, set[str]], dialect: str = "duckdb") -> list[Definition]`
    in select-list order; `visible` maps table → visible column names.
  - `definition_columns(definition: Definition, dialect: str = "duckdb") -> set[tuple[str, str]]`
    (table, column) pairs it reads.
  - `uses_metric(sql: str, definition: Definition, dialect: str = "duckdb") -> bool`.

- [ ] **Step 1: Write the failing tests** in `tests/unit/test_metric.py`:
  - `test_sum_with_where_gives_measure_and_filters`: `SELECT SUM(thanh_tien) FROM orders WHERE trang_thai <> 'Hủy' AND ngay >= DATE '2026-05-01' AND ngay < DATE '2026-06-01'` → one candidate, `table == "orders"`, `filters` has one entry naming `trang_thai`, none naming `ngay`.
  - `test_case_condition_stays_inside_the_measure`: `SUM(CASE WHEN trang_thai = 'Xong' THEN thanh_tien ELSE 0 END)` → `filters == []`, measure contains `trang_thai`.
  - `test_each_aggregate_is_a_candidate_and_group_by_is_ignored`: `SELECT nhom, SUM(a), COUNT(*) FROM t GROUP BY nhom ORDER BY 2 DESC LIMIT 5` → two candidates.
  - `test_no_candidate_from_join_subquery_cte_window_or_union` (parametrised, each → `[]`).
  - `test_column_not_visible_drops_the_candidate`.
  - `test_round_trip_json`.
  - `test_extra_filters_still_match`: definition from the first test; `uses_metric` on the same SUM with that filter plus `san_pham = 'Trà'` and a June period → `True`.
  - `test_equivalent_spelling_matches`: `sum("thanh_tien")` and `trang_thai != 'Hủy'` in another conjunct order → `True`.
  - `test_missing_filter_or_other_measure_or_table_does_not_match` (three cases → `False`).
  - `test_definition_filter_inside_case_condition_matches`: definition with filter `trang_thai <> 'Hủy'`, query `SUM(thanh_tien) FILTER (WHERE trang_thai <> 'Hủy')` → `True`.
  - `test_unparseable_sql_matches_nothing_and_gives_no_candidates`.

- [ ] **Step 2: Run** `uv run pytest tests/unit/test_metric.py -v`. Expected: FAIL, module not found.

- [ ] **Step 3: Implement** the module. Canonical SQL: strip aliases and table qualifiers, apply
  `sqlglot.optimizer.normalize_identifiers`, render with `.sql(dialect=dialect, identify=True)`;
  normalise `!=` to `<>` through sqlglot's own rendering and `COUNT(1)` to `COUNT(*)`. Date
  conjuncts are those `date_bound` reads, as in `explain.py`. Facts come from `explain_sql` on the
  same query: the measure's fact by position among `measures`, and the filters list. Matching: a
  select-list aggregate's canonical SQL equals `definition.measure`, the table matches, and every
  definition filter is in the canonical top-level `WHERE` conjuncts or the canonical conjuncts of
  that aggregate's `FILTER`/`CASE` condition.

- [ ] **Step 4: Run** `uv run pytest tests/unit/test_metric.py -v && uv run ruff check src/da_agent/reports/metric.py`. Expected: PASS, no lint errors.

- [ ] **Step 5: Commit** `feat: read metric definitions from a query and match them against queries`.

### Task 2: Storage for metric entries

**Files:**
- Create: `src/da_agent/storage/migrations/0012_metric_definitions.sql`
- Modify: `src/da_agent/storage/memory.py`
- Test: `tests/integration/test_memory.py`

**Interfaces:**
- Consumes: `Definition` (Task 1).
- Produces:
  - `MemoryKind` gains `"metric"`. `MemoryEntry` gains `name: str | None`, `aliases: list[str] | None`, `definition: dict | None` (all SELECTs return them).
  - `MemoryStore.save_metric(workspace_id, dataset_id, *, name: str, aliases: list[str], definition: dict, text: str, author: str, source_run_id: UUID | None) -> UUID`
  - `MemoryStore.rename_metric(workspace_id, entry_id, *, name: str, aliases: list[str], author: str) -> UUID` (new entry superseding the old, like `edit`).
  - `metric_key(name: str) -> str` (accents removed, case folded, spaces collapsed).

- [ ] **Step 1: Write the failing tests**:
  - `test_save_metric_is_approved_and_listed` (fields round-trip, `status == "approved"`).
  - `test_same_name_without_accents_supersedes`: save "doanh thu", then "Doanh Thu" → `approved()` has one metric, the old one's `superseded_by` is the new id.
  - `test_name_matching_an_alias_supersedes`: aliases `["DT"]`, then name "dt" → one live metric.
  - `test_rename_keeps_definition_and_supersedes`.
  - `test_metric_requires_name_and_definition` (DB check; and `MemoryStateError` for name > 80, 11 aliases, empty alias).
  - `test_rename_rejects_non_metric`.
  - `test_other_workspace_cannot_see_or_rename`.

- [ ] **Step 2: Run** `uv run --env-file .env pytest tests/integration/test_memory.py -v`. Expected: new tests FAIL.

- [ ] **Step 3: Implement.** Migration: drop and re-add the `kind` check with `metric`; add
  `name text CHECK (length(btrim(name)) BETWEEN 1 AND 80)`, `aliases text[] CHECK (cardinality(aliases) <= 10)`,
  `definition jsonb`, and `CHECK (kind <> 'metric' OR (name IS NOT NULL AND definition IS NOT NULL))`.
  `save_metric` supersedes, in the same transaction, every live metric of the dataset whose
  `metric_key` of name or any alias equals the key of the new name or any new alias.

- [ ] **Step 4: Run** the same command plus `uv run pytest tests/unit -q`. Expected: PASS.

- [ ] **Step 5: Commit** `feat: store metric definitions as dataset memory`.

### Task 3: Metrics reach the model first; stale ones do not

**Files:**
- Modify: `src/da_agent/agent/memory.py`, `src/da_agent/worker/memory.py`, `src/da_agent/worker/runs.py:70-73`
- Test: `tests/unit/test_memory_documents.py`, `tests/integration/test_worker.py`

**Interfaces:**
- Consumes: `Definition`, `definition_columns` (Task 1); `MemoryEntry` fields (Task 2).
- Produces: `MemoryNote` gains optional `name: str | None = None`, `aliases: tuple[str, ...] = ()`,
  `definition: Definition | None = None`. `MemoryLoader.__call__(job, visible: dict[str, set[str]] | None = None)`.

- [ ] **Step 1: Write the failing tests**:
  - `test_metric_section_comes_first_with_name_aliases_measure_and_filters`: asserts the heading `## Metric 1: Doanh thu (also: doanh số, DT)`, the measure line, the filter line, the table name, and that it precedes a correction.
  - `test_preface_mentions_metrics_only_when_there_are_some`.
  - `test_documents_without_metrics_are_unchanged`: the rendered text for a correction, an alias, and an example equals a literal copy of today's output (capture it before changing code).
  - `test_metric_name_cannot_forge_a_section` (name with a newline and `## `).
  - `test_stale_metric_is_left_out` (worker loader, Postgres): a metric naming a column absent from `visible` is not in the documents, and a warning is logged.

- [ ] **Step 2: Run** `uv run pytest tests/unit/test_memory_documents.py -v`. Expected: new tests FAIL; `test_documents_without_metrics_are_unchanged` passes before and after.

- [ ] **Step 3: Implement.** Section format and preface sentence exactly as in the spec ("Reaching
  the model"). Names and aliases go through `" ".join(x.split())`. `_KIND_ORDER` puts `metric`
  at 0. `RunHandler` passes `{t.name: {c.name for c in t.columns} for t in dataset.describe()}`.

- [ ] **Step 4: Run** `uv run pytest tests/unit -q && uv run --env-file .env pytest tests/integration/test_worker.py -q`. Expected: PASS.

- [ ] **Step 5: Commit** `feat: runs read metric definitions first and skip stale ones`.

### Task 4: API: candidates, save, rename, list, badge

**Files:**
- Modify: `src/da_agent/api/app.py` (`_explanations`, memory routes, `_memory_view`, `MemoryEditRequest`)
- Test: `tests/integration/test_api_metrics.py` (new), `tests/integration/test_api_explanations.py`

**Interfaces:**
- Consumes: Tasks 1–2.
- Produces (HTTP):
  - Each item of the run view's `explanations` gains `metric_candidates: [{index, facts}]` and
    `metrics_used: [{id, name}]`. `index` is the candidate's position from `metric_candidates`.
  - `POST /v1/datasets/{id}/metrics` body `{run_id, query_index, candidate_index, name, aliases}` → 201 `{id}`.
  - `PATCH /v1/memory/{id}` accepts `{name, aliases}` for a metric (→ `rename_metric`); `text`/`sql` for a metric → 422.
  - `GET /v1/datasets/{id}/memory` entries gain `name`, `aliases`, `facts`, `stale`.

- [ ] **Step 1: Write the failing tests**:
  - `test_explanation_lists_candidates` and `test_no_candidates_for_a_join`.
  - `test_save_metric_from_a_run`: 201; listed with name, facts, `stale is False`; audit has `memory.create` with `kind=metric`.
  - `test_save_rejects_wrong_run_dataset_or_index` (422 for index out of range or run on another dataset; 404 for another workspace's run; 409 for a run that is not succeeded).
  - `test_body_cannot_carry_sql` (extra `definition` field → 422, `extra="forbid"`).
  - `test_rename_metric` and `test_metric_text_is_read_only`.
  - `test_badge_only_for_metrics_in_snapshot`: run A created before saving, run B after → only B's explanation has `metrics_used`.
  - `test_memory_list_marks_stale`: change the dataset spec so a column is gone → `stale is True`.

- [ ] **Step 2: Run** `uv run --env-file .env pytest tests/integration/test_api_metrics.py tests/integration/test_api_explanations.py -v`. Expected: new tests FAIL.

- [ ] **Step 3: Implement.** Visible columns come from the dataset spec (tables' columns not
  `sensitive`), shared by candidates and the stale flag. The save route rebuilds the candidate
  from the stored run, writes `text` as a plain fallback sentence (`"<measure fact>; filters: …"`
  built in Python is enough, the web phrases its own), and logs `memory.create`. `metrics_used`
  loads the metrics among the job's `memory_ids`.

- [ ] **Step 4: Run** `uv run --env-file .env pytest tests/integration -q`. Expected: PASS.

- [ ] **Step 5: Commit** `feat: save metric definitions from an answer over HTTP and mark answers that use them`.

### Task 5: Web: save dialog, badge, Definitions section

**Files:**
- Create: `web/src/metrics.ts`, `web/src/metrics.test.ts`, `web/src/components/chat/save-metric-dialog.tsx`
- Modify: `web/src/api.ts`, `web/src/components/chat/evidence.tsx` (or wherever "How it was calculated" renders), `web/src/components/memory-panel.tsx`, `web/src/memory.ts`

**Interfaces:**
- Consumes: Task 4 HTTP shapes; `describeExpr`, `describeFilter` (`explain.ts`).
- Produces: `metricSentence(facts): string`; `validateMetricName(name, aliasesText): { name, aliases } | { error }`
  (aliases split on commas, trimmed, deduplicated, limits from Global Constraints);
  `groupMemory` returns `definitions` separately from other active notes.

- [ ] **Step 1: Write the failing tests** in `metrics.test.ts` and `memory.test.ts`:
  sentence for SUM with one filter reads `Sum of Thành tiền, only rows where Trạng thái is not Hủy`
  (filter wording from `describeFilter`); validation errors for empty name, 81 characters, 11 aliases;
  `groupMemory` puts live metrics in `definitions`, superseded ones in `history`.

- [ ] **Step 2: Run** `cd web && npm test`. Expected: new tests FAIL.

- [ ] **Step 3: Implement.** Copy: button "Save as a definition"; dialog title "Save a definition",
  fields "Name" and "Other names (comma-separated)", note "Used from your next question.";
  badge "Used your definition of {name}" linking to the panel; panel section "Definitions" first,
  stale warning "The data changed. Save this definition again from a new answer."

- [ ] **Step 4: Run** `cd web && npm test && npm run lint && npm run build`. Expected: PASS.

- [ ] **Step 5: Browser check** with the `run` skill on a demo dataset: ask a revenue question,
  save the definition, ask again, see the badge; check the panel at 1440 and 390 px, light and dark.

- [ ] **Step 6: Commit** `feat: save, use, and manage metric definitions in the web UI`.

### Task 6: Records and effect eval

**Files:**
- Create: `docs/adr/0036-metric-definitions.md`, `docs/learning/16-dinh-nghia-chi-so.md`
- Modify: `docs/adr/README.md`, `docs/learning/README.md`, `docs/progress.md`, `docs/architecture.md` (memory section: one line on metrics)

- [ ] **Step 1:** Write ADR 0036 (context, decision, approaches A/B/C, consequences) and the
  Vietnamese learning note (concept, why A, rejected options, interview questions).
- [ ] **Step 2: Ask the user** before the effect eval: about 20 dev questions whose failure was
  business-rule misuse, run with and without definitions saved from correct runs (private
  competition folder; never from `answers.jsonl` or holdout shops), and the expected cost.
- [ ] **Step 3:** After approval, run it, report correct/wrong/abstain both ways and the cost, and
  publish only aggregates in `docs/eval/`.
- [ ] **Step 4:** Update `progress.md` (under 60 lines). Commit `docs: ADR 0036 and notes for metric definitions`.
