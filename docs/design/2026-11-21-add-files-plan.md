# Add files to a dataset: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user add files to a `ready` dataset while chatting. Each upload makes a new dataset
version. The version is applied at once when it is safe, and waits for the user when there is
overlap, a new table, a lost table, or a quality drop.

**Architecture:**
- A `dataset_versions` table. Each version has its own upload folder and build directory
  (`<id>-v<n>`); version 1 of an existing dataset keeps `<id>`.
- A `version` job ingests all of the version's files in the sandbox and matches raw tables to the
  current version by header. It builds with the approved spec plus overlap checks, then decides
  in code.
- The model is called only to propose specs for new tables or repairs to failing columns. Code
  merges its output and limits what it may change.
- Runs record the version they read. Query jobs and exports reuse that version.

**Tech Stack:** Python 3.12, FastAPI, Postgres (psycopg), DuckDB in the sandbox, React + Vite +
TypeScript, vitest.

**Spec:** [2026-11-21-add-files.md](2026-11-21-add-files.md)

## Global Constraints

- **No new dependencies.**
- **Untrusted bytes are parsed only in the sandbox (ADR 0009).** The Runner's mounts and
  `_child` check do not change. Version directories are siblings named `<dataset id>-v<n>`.
- **`DatasetSpec` stays declarative.** New fields compile to SQL with `q()` and `lit()` only.
- **Nothing is dropped silently (ADR 0010).** Rows removed by `drop_overlap` go to `_excluded`
  with reason `already in <file>`.
- **No dedupe switched on by itself.** The version build never calls `_with_dedupe`.
- **Quality thresholds are those of first ingestion:**
  - `MIN_PARSE_RATE = 0.98`;
  - `MAX_EXCLUDED_SHARE = 0.10`;
  - the full-row and identifier signals of `_record_problems`.
- **Spec preservation.** Existing table specs are copied byte for byte, except repairs the user
  accepted. A repair may change only `type`, `date_formats`, and `true_values` of a failing
  column, and the failing `exclude_rows` rules.
- **Limits:**
  - at most one open version per dataset (409);
  - at most `max_dataset_versions = 20` per dataset (409);
  - `max_files` (50) counts every file of the new version;
  - `max_file_bytes` per file.
- **Audit events:** `dataset.files_add`, `dataset.version_approve`, `dataset.version_discard`.
- **UI text is in English.** Code and docs are in English; the learning note is in Vietnamese.
- **Tests:**
  - `uv run pytest` for unit tests.
  - Postgres tests need `uv run --env-file .env pytest`.
  - Web tests: `cd web && npm test`.
  - No real model call without asking the user.

## Review Focus

1. **The same file sent twice** (stored as `orders_1.csv`): every row overlaps exactly. The user
   is asked; rows are never added twice. Test in Task 5.
2. **A run in flight when a version is applied** finishes on the version it started with. The
   next run uses the new one. Test in Task 6.
3. **The worker dies after installing the build directory but before the apply transaction.** The
   retry applies once and does not fail on the existing directory. Test in Task 5.
4. **"Replace" on the only file of a table:** the table is removed, the version waits as
   `proposed`, and the review lists metrics that would turn stale. Test in Task 5.
5. **One xlsx whose first sheet matches an existing table and whose second sheet is new:** one
   grown table and one new table in the same version. Test in Task 2.

---

### Task 1: Overlap checks and `drop_overlap` in the build

**Files:**
- Modify:
  - `src/da_agent/ingestion/spec.py` (`OverlapDrop`, `TableSpec.drop_overlap`);
  - `src/da_agent/ingestion/build.py` (`build(..., new_sources=...)`, overlap report, apply drops);
  - `src/da_agent/runner/runner.py` (`BuildRun.new_sources`);
  - `sandbox/da_sandbox/__main__.py` (`run_build` passes `new_sources`).
- Test: `tests/unit/test_dataset_build.py`, `tests/unit/test_dataset_spec.py`,
  `tests/security/test_sandbox_ingest.py`.

**Interfaces:**
- Produces, in `spec.py`:
  - `OverlapDrop(file: str, against: list[str], key: list[str])`. `file` and `against` are stored
    file names. `key` holds raw column names; empty means the whole row.
  - `TableSpec.drop_overlap: list[OverlapDrop]`, at most 20.
- Produces, in `build.py`:
  - `Overlap(kind: Literal["exact", "key", "date"], rows: int, key: list[str] = [],
    column: str | None = None, examples: list[dict[str, str | None]])`, with at most 5 examples.
  - `TableBuild.overlaps: list[Overlap]` and `TableBuild.new_rows: int`.
  - `TableBuild.new_columns: list[ColumnBuild]`: parse stats over the rows from new sources only,
    for the quality check.
- Produces `build(raw_database, spec, report, out_dir, new_sources: frozenset[str] = frozenset())`.
  A row is new when the file part of its `_source` (the text before `#`) is in `new_sources`.
- Produces `BuildRun.new_sources: list[str]`, with at most 1000 items, each matching the stored
  name pattern.

**Steps:**
- [ ] Write the tests:
  - `test_overlap_exact_counts_resent_rows`: old `orders.csv` with 3 rows, new `orders_1.csv`
    with the same 3 rows. `overlaps == [Overlap(kind="exact", rows=3, ...)]` and `new_rows == 3`.
  - `test_overlap_key_numeric_order_id`: integer `order_id` and a changed `status`. A `key`
    overlap with `key == ["order_id"]` and the right row count.
  - `test_overlap_key_two_columns_for_line_items`: `order_id` repeats per line, and
    `(order_id, product)` is the key.
  - `test_overlap_date_range`: new rows dated inside the old min/max give a `date` overlap on
    that column. New rows after the old max give none.
  - `test_no_overlap_without_new_sources`: `build()` with no `new_sources` reports none, and its
    output is unchanged from today (the existing build tests still pass).
  - `test_overlap_ignores_dedupe_flag`: with `dedupe: exact`, the exact overlap is still counted.
  - `test_drop_overlap_moves_rows_to_excluded`: the dropped rows appear in `_excluded` with
    reason `already in orders.csv`, and the clean table keeps the old rows.
  - `test_drop_overlap_quotes_names`: a file name and a key column containing `'` and `"` build
    without error and match exactly.
  - `test_spec_rejects_bad_overlap_drop`: an empty `file` and more than 20 entries are both
    refused.
  - Sandbox test: a `BuildRun` with `new_sources` returns a report with `overlaps`.
- [ ] Run them: they fail.
- [ ] Implement:
  - Exact and key overlaps run on non-excluded raw rows, after exclusion and before fill-down
    and dedupe.
  - Key search: the smallest set of 1 or 2 raw columns, numbers allowed, whose distinct
    non-blank values are at least 99% of the old non-excluded rows. Search the first
    `KEY_SEARCH_COLUMNS` columns, as `_distinct_row_key` does.
  - Date overlap: on each date column of the clean table, new rows whose date lies within the
    old rows' min and max.
  - `drop_overlap` marks `__excluded` in the marked table, before fill-down.
- [ ] Run them: they pass. Run `uv run pytest tests/unit tests/security -k "build or spec or
  sandbox_ingest"`.
- [ ] Commit: `feat: overlap checks and drop_overlap in the dataset build`.

### Task 2: Matching and merging versions (pure code)

**Files:**
- Create: `src/da_agent/ingestion/versioning.py`
- Test: `tests/unit/test_versioning.py`

**Interfaces:**
- Consumes `IngestReport`, `BuildReport`, and `TableBuild.overlaps` and `new_columns` from Task 1.
- Produces:
  - `TableHeader(raw_name: str, header: tuple[str, ...])`, and
    `headers(report: IngestReport) -> list[TableHeader]`, where the header excludes
    `META_COLUMNS`.
  - `TableChange(raw_name: str, table: str | None, kind: Literal["unchanged", "grown", "new",
    "removed"], previous_raw_name: str | None, near_match: str | None, missing_columns:
    list[str], extra_columns: list[str])`.
  - `match_tables(previous: list[TableHeader], current: IngestReport, new_sources:
    frozenset[str], spec: DatasetSpec) -> list[TableChange]`. Matching is by equal header
    tuples. A `new` table whose header shares at least half its columns with an existing table
    sets `near_match` to that table's name, plus the column differences.
  - `rename_sources(spec: DatasetSpec, changes: list[TableChange]) -> DatasetSpec`: points each
    kept table spec at its new raw name and drops removed tables, along with relationships that
    touch them.
  - `quality_problems(report: BuildReport, grown: set[str]) -> dict[str, list[str]]`: per
    grown table, the problems from `new_columns` and from `excluded` on new rows. It uses
    `parse_problems`' thresholds and message wording.
  - `merge_new_tables(current: DatasetSpec, proposed: DatasetSpec, new_raw: set[str]) ->
    DatasetSpec`: keeps every current table spec as it is; adds only proposed tables whose
    `source` is in `new_raw`; adds only proposed relationships with at least one end in a new
    table.
  - `RepairChange(table: str, path: str, before: Any, after: Any)`. `path` is
    `columns.<source>.type`, `columns.<source>.date_formats`, `columns.<source>.true_values`, or
    `exclude_rows.<position>`.
  - `repair_diff(current: DatasetSpec, proposed: DatasetSpec, failing: dict[str, set[str]]) ->
    list[RepairChange]`. `failing` maps a table to its failing column sources and
    `exclude_rows.<n>` keys. Any change outside it is left out, and the caller logs it.
  - `apply_repairs(spec: DatasetSpec, accepted: list[RepairChange]) -> DatasetSpec`.

**Steps:**
- [ ] Write the tests:
  - `test_match_new_file_that_renames_group`: old raw table `orders` from `orders.csv`; new
    upload adds `a_orders.csv` with the same header, so the raw table becomes `a_orders`. It is
    matched as `grown`, `previous_raw_name == "orders"`, and `rename_sources` points the spec's
    `orders` table at `a_orders`.
  - `test_xlsx_with_matching_and_new_sheet` (Review Focus 5): one `grown` and one `new`.
  - `test_near_match_is_new_with_differences`: one added column. `kind == "new"`,
    `near_match == "orders"`, and `extra_columns == ["discount"]`.
  - `test_removed_table`: an old header that is missing now gives `removed`. `rename_sources`
    drops its table and relationships.
  - `test_merge_keeps_existing_tables_exactly`: the model's proposal edits an existing table,
    and `merge_new_tables` returns that table equal to the current one.
  - `test_merge_drops_relationships_between_old_tables`.
  - `test_repair_diff_rejects_other_fields`: a proposed change to a non-failing column, or to
    `name`, `description`, or `fill_down`, is left out.
  - `test_apply_repairs_changes_only_accepted`.
  - `test_quality_problems_new_rows_only`: old rows parse at 100%, new rows at 50%. A problem
    is reported for the grown table and none for the others.
- [ ] Run them: they fail.
- [ ] Implement.
- [ ] Run them: they pass.
- [ ] Commit: `feat: match, merge, and repair dataset versions`.

### Task 3: Model proposals for new tables and repairs

**Files:**
- Create: `src/da_agent/ingestion/propose_changes.py`
- Test: `tests/unit/test_propose_changes.py`

**Interfaces:**
- Consumes `propose_spec`'s helpers (`profile_for_prompt`, `_parse`, `sanitize_spec`,
  `ProposalResult`) and Task 2's `merge_new_tables` and `repair_diff`.
- Produces:
  - `propose_new_tables(report: IngestReport, new_raw: set[str], current: DatasetSpec,
    documents: list[dict[str, str]], llm: LLMClient, *, run_id: str, validate:
    Callable[[DatasetSpec], list[str]], max_attempts: int = 3, budget: RunBudget | None =
    None) -> ProposalResult`. The prompt holds the profile of the new raw tables only, plus the
    current spec's table names, columns, and relationships, inside `<existing_spec>`, marked
    read-only. The returned spec is already merged.
  - `propose_repairs(report: BuildReport, failing: dict[str, list[str]], current: DatasetSpec,
    llm: LLMClient, *, run_id: str, validate: Callable[[DatasetSpec], list[str]],
    max_attempts: int = 3, budget: RunBudget | None = None) -> tuple[list[RepairChange], str |
    None]`, giving the changes and an error.

**Steps:**
- [ ] Write the tests, with the scripted fake LLM used in `tests/unit/test_spec_proposal.py`:
  - `test_new_tables_prompt_lists_only_new_profiles`: the old table's samples are not in the
    prompt.
  - `test_new_tables_existing_spec_kept_when_model_rewrites_it`.
  - `test_new_tables_repair_loop_uses_validate`: the first reply fails `validate` and the second
    passes, so `attempts == 2`.
  - `test_new_tables_budget_error_returns_error`.
  - `test_repairs_only_failing_fields`: the model also changes another column, which is left out.
  - `test_repairs_none_when_model_cannot_fix`: every attempt fails `validate`, so the result is
    `([], "<reason>")`.
  - `test_cell_text_is_delimited`: a sample value containing an instruction appears only inside
    the profile block.
- [ ] Run them: they fail.
- [ ] Implement. Reuse `SYSTEM_PROMPT`'s rules and add one paragraph per task.
- [ ] Run them: they pass.
- [ ] Commit: `feat: model proposals for new tables and repairs`.

### Task 4: Migration and version store

**Files:**
- Create:
  - `src/da_agent/storage/migrations/0017_dataset_versions.sql`;
  - `src/da_agent/storage/dataset_versions.py`.
- Modify: `src/da_agent/storage/jobs.py` (`JobKind` adds `"version"`).
- Test: `tests/integration/test_dataset_versions_store.py`

**Interfaces:**
- Migration:
  - `dataset_versions(dataset_id uuid REFERENCES datasets ON DELETE CASCADE, version int,
    status text CHECK IN ('uploaded','checking','needs_choice','proposed','building',
    'applied','failed','discarded'), files jsonb, upload_dir text, build_dir text, spec jsonb,
    summary jsonb, documents jsonb, tables jsonb, changes jsonb, choices jsonb, repairs jsonb,
    error text, created_at, updated_at, PRIMARY KEY (dataset_id, version))`.
  - A unique partial index allows one open version per dataset (status not in `applied`,
    `failed`, `discarded`).
  - `datasets.current_version int`.
  - Backfill: version 1, `applied`, for every `ready` dataset, with `upload_dir = build_dir =
    id::text`.
  - The jobs kind check adds `'version'`.
- Produces `DatasetVersionStore(conn)`, with methods:
  - `open_version(workspace_id, dataset_id, files, upload_dir, max_versions) -> int`. Raises
    `DatasetStateError` when the dataset is not ready, when a version is open, or when the limit
    is reached. Enqueues `version` with `{"dataset_id", "version", "step": "check"}`.
  - `get(workspace_id, dataset_id, version) -> VersionRecord | None`.
  - `list(workspace_id, dataset_id) -> list[VersionRecord]`.
  - `current(dataset_id) -> VersionRecord`.
  - `mark(dataset_id, version, status, **fields)`.
  - `apply(dataset_id, version, build_dir)`, in one transaction. It sets the version to
    `applied` and copies `spec`, `summary`, and `documents` onto `datasets`. It sets
    `current_version` and the `files` list. Applying an already applied version is a no-op.
  - `approve(workspace_id, dataset_id, version, choices, spec_edits, accepted_repairs) -> str`.
    It is allowed only from `needs_choice` or `proposed`. It stores the choices and enqueues
    `version` with `"step": "build"`, then returns `"building"`.
  - `discard(workspace_id, dataset_id, version)`.
- Produces `VersionRecord`, a frozen dataclass with every column.

**Steps:**
- [ ] Write the tests:
  - The backfill makes version 1 for an existing ready dataset.
  - A second open version raises.
  - The 21st version raises.
  - A dataset that is not ready raises.
  - Applying twice is a no-op.
  - `approve` from `checking` raises.
  - `discard` frees the slot.
  - A version from another workspace is never returned.
- [ ] Run them: they fail. Use `uv run --env-file .env pytest
  tests/integration/test_dataset_versions_store.py`.
- [ ] Implement.
- [ ] Run them: they pass.
- [ ] Commit: `feat: dataset versions table and store`.

### Task 5: The version job

**Files:**
- Create: `src/da_agent/worker/versions.py` (`VersionHandler`)
- Modify:
  - `src/da_agent/worker/__main__.py` (register `"version"`);
  - `src/da_agent/worker/datasets.py` (factor `_install` to accept a target name).
- Test: `tests/integration/test_worker_versions.py`

**Interfaces:**
- Consumes Tasks 1–4.
- Produces `VersionHandler(url, schema, runner, llm, datasets_root, uploads_root, budgets)`,
  which is called as `(job, lost) -> dict`.
- **Step `check`:**
  1. Ingest `upload_dir`.
  2. Get the previous headers from the current version's `tables`. When that field is empty,
     ingest the current `upload_dir` first and store its headers.
  3. Run `match_tables`, then `rename_sources`.
  4. Build with `new_sources`.
  5. Decide:
     - overlaps lead to `needs_choice`;
     - new tables lead to `propose_new_tables`, then `proposed`;
     - removed tables or columns lead to `proposed`, and `changes` lists stale metrics through
       `is_stale`;
     - quality problems lead to `propose_repairs`, then `proposed`;
     - when none of these apply, install as `<id>-v<n>` and `apply`.
- **Step `build`:**
  1. Apply the choices: `replace` removes the replaced file from `files` and its link from
     `upload_dir`; `drop_overlap` adds `OverlapDrop` entries; `keep_both` changes nothing.
  2. Apply the user's edits to new tables and the accepted repairs.
  3. Re-ingest if files changed, build, install, and `apply`.
  4. A build that still has quality problems is applied only when the user chose "apply
     anyway"; otherwise it is `failed`.
- **Stored `changes` JSON** has one entry per table:
  `{"table", "kind", "new_rows", "overlaps", "near_match", "missing_columns", "extra_columns",
  "quality", "stale_metrics"}`.
- **Failures:** they mark the version `failed` with a message from `FAILURES`, and the job
  completes. The current version is untouched. The failed version's `upload_dir`, staging
  outputs, and any installed `<id>-v<n>` are removed.
- **Tracing:** spans for ingest, match, build, propose, and install, with model calls under the
  job's run id, as `IngestHandler` traces today.

**Steps:**
- [ ] Write the tests, with the fake Runner and fake LLM of `tests/integration/test_worker.py`:
  - `test_same_shape_applies_without_model_call`: the fake LLM fails if called. The new
    current version is 2 and `datasets_root/<id>-v2` exists.
  - `test_resent_file_waits_for_choice` (Review Focus 1).
  - `test_new_table_proposes_and_keeps_old_spec`.
  - `test_replace_only_file_removes_table_and_lists_stale_metric` (Review Focus 4): a saved
    metric on the removed table appears in `stale_metrics`.
  - `test_quality_drop_proposes_repair`: the new file uses `mdy` dates and the repair is stored
    as a `RepairChange`.
  - `test_build_step_applies_choices`: for `drop_overlap`, the excluded rows are in the build;
    for `replace`, the old file is gone from `files`.
  - `test_failure_keeps_current_version`: an ingest failure leaves the version `failed` and
    `current_version` unchanged.
  - `test_retry_after_install_applies_once` (Review Focus 3): the first attempt is killed after
    `_install`, and the second attempt succeeds with one applied version.
  - `test_model_cost_charged_to_job`: ledger rows carry the job id.
- [ ] Run them: they fail.
- [ ] Implement.
- [ ] Run them: they pass.
- [ ] Commit: `feat: version job checks, proposes, and applies dataset versions`.

### Task 6: Runs read a fixed version

**Files:**
- Modify:
  - `src/da_agent/api/app.py` (`create_run` payload, `_run_view`);
  - `src/da_agent/worker/datasets.py` (`DatasetOpener`);
  - `src/da_agent/worker/runs.py`;
  - `src/da_agent/worker/queries.py`;
  - `src/da_agent/worker/exports.py`;
  - `src/da_agent/storage/exports.py` (`dataset_changed_since_run`);
  - `src/da_agent/storage/memory.py` (`recent_turns`, `Turn`).
- Test: `tests/integration/test_worker.py`, `tests/integration/test_worker_queries.py`,
  `tests/integration/test_worker_exports.py`, `tests/integration/test_memory.py`.

**Interfaces:**
- **Run payload:** `create_run` adds `dataset_version: current_version`. Old payloads without it
  mean version 1.
- **Opener:** `DatasetOpener(workspace_id, dataset_id, version: int)` opens that version's
  `build_dir`. The version must be `applied` and belong to the dataset.
- **Query and export jobs** open the source run's version, so an old answer gives its old
  numbers.
- **`dataset_changed_since_run`** returns true only when that version's build directory is gone.
- **`_run_view`** gains `dataset_version`.
- **Turns:** `Turn` gains `dataset_version: int`. `recent_turns(..., current_version: int)` adds
  this line to each older turn's answer: `(answered on data version {n}; the data has changed
  since; do not reuse its numbers)`.

**Steps:**
- [ ] Write the tests:
  - `test_run_in_flight_keeps_its_version` (Review Focus 2): a run is created on v1, v2 is
    applied, and the run then executes against v1's directory.
  - `test_new_run_uses_current_version`.
  - `test_query_job_uses_run_version`.
  - `test_export_of_old_run_reads_old_version`.
  - `test_old_payload_without_version_opens_v1`.
  - `test_recent_turns_label_older_versions`: the label appears on v1 turns and not on v2 turns.
- [ ] Run them: they fail.
- [ ] Implement.
- [ ] Run them: they pass. Then run `uv run --env-file .env pytest tests/integration`.
- [ ] Commit: `feat: runs, queries, and exports read the dataset version they started on`.

### Task 7: HTTP API

**Files:**
- Modify: `src/da_agent/api/app.py`, `src/da_agent/config.py` (`max_dataset_versions`,
  `DA_AGENT_MAX_DATASET_VERSIONS`).
- Test: `tests/integration/test_api_versions.py`

**Interfaces:**
- `POST /v1/datasets/{id}/files` (multipart): fields `files`, and an optional `replaces` per
  file, which repeats in upload order and is empty for "add".
  - The API checks `storage_name` against all names in the version.
  - It makes the folder `<id>-v<n>`, with kept files hard-linked from the current `upload_dir`
    (falling back to a copy) and new files saved.
  - It calls `open_version` and writes the audit event `dataset.files_add`.
  - It returns `202 {"dataset_id", "version", "status": "uploaded"}`.
  - On any error the folder is removed.
- `GET /v1/datasets/{id}/versions` returns `{"versions": [...]}`, each with `version`,
  `status`, `files`, `changes`, `repairs`, `error`, and `created_at`.
- `POST /v1/datasets/{id}/versions/{n}/approve` takes the body `{"choices": {table:
  "replace"|"drop_overlap"|"keep_both"}, "replace_files": {table: stored name}, "spec":
  edited new-table specs or null, "repairs": [accepted RepairChange], "apply_anyway": bool}`.
  - It returns 409 when the version is not waiting.
  - It returns 422 on invalid edits, including edits to existing tables.
- `POST /v1/datasets/{id}/versions/{n}/discard` returns 204. It removes the version's
  `upload_dir`, and its build directory if one was installed. It returns 409 while the version
  is `checking` or `building`.
- `GET /v1/datasets/{id}` adds `current_version` and `open_version` (the open version's view,
  or null).

**Steps:**
- [ ] Write the tests:
  - The happy path enqueues a `version` job.
  - A dataset that is not ready returns 409.
  - A second open version returns 409.
  - More than `max_files` over the whole version returns 413.
  - A `replaces` that names an unknown file returns 400.
  - A path traversal name is stored safely.
  - Another workspace gets 404 on every route.
  - Approve from the wrong state returns 409.
  - An edit to an existing table returns 422.
  - Discard frees the slot.
  - The audit rows are written.
  - The rate and money limits apply (`_may_start_job`).
- [ ] Run them: they fail.
- [ ] Implement.
- [ ] Run them: they pass.
- [ ] Commit: `feat: API to add files and review dataset versions`.

### Task 8: Web UI

**Files:**
- Create:
  - `web/src/components/chat/add-files-dialog.tsx`;
  - `web/src/components/chat/version-status.tsx`;
  - `web/src/VersionReview.tsx`;
  - `web/src/versions.ts`;
  - `web/src/versions.test.ts`.
- Modify: `web/src/api.ts`, `web/src/Conversation.tsx`, `web/src/DatasetView.tsx`.

**Interfaces:**
- **`api.ts`** gains:
  - `addFiles(datasetId, files: File[], replaces: (string | null)[])`;
  - `listVersions(datasetId)`;
  - `approveVersion(datasetId, n, body)`;
  - `discardVersion(datasetId, n)`;
  - types `DatasetVersion` and `VersionStatus`.
- **`versions.ts`:**
  - `versionDividers(runs: {id: string; dataset_version: number}[]) -> Map<string, number>`
    gives the run id before which a divider shows, and its new version.
  - `isOlderVersion(run, current)`.
  - `statusLine(version)`. Its fixed text:
    - `"Checking new files…"`;
    - `"Added {n} rows to {table} (parse rate {p}%)"`;
    - `"Your new files need a decision"`;
    - `"Couldn't add files: {error}"`.
- **Chat:**
  - An **Add files** button sits next to the composer.
  - The dialog has a file drop and, per file, a select between **Add** and **Replace <file>**.
  - A status card shows while a version is open, and polls `GET /datasets/{id}` every 3 s.
  - A divider reads `Added {files}. Answers below use data version {n}.`
  - A `data v{n}` badge marks older answers.
- **Review screen:**
  - overlap per table, with examples and three choices: **Replace old file**, **Drop overlapping
    rows**, **Keep both**;
  - new tables in the spec editor, with only the new tables editable; a near match shows
    `Looks like {table}, but columns differ: +{extra} −{missing}. It will be a separate table.`;
  - repairs as before/after rows with accept checkboxes;
  - removed tables and stale metrics;
  - **Apply** and **Discard** buttons, plus **Apply anyway** when a repair failed.

**Steps:**
- [ ] Write `versions.test.ts`:
  - dividers for runs on versions 1, 1, 2, 2, 3;
  - a run without a version counts as 1;
  - each status line's text.
- [ ] Run `cd web && npm test`: it fails.
- [ ] Implement.
- [ ] Run `npm test` and `npm run build`: they pass.
- [ ] Commit: `feat: add files from the chat and review dataset versions`.

### Task 9: Docs and end-to-end check

**Files:**
- Create:
  - `docs/adr/0045-dataset-versions.md`;
  - `docs/learning/24-dataset-versions.md` (Vietnamese).
- Modify:
  - `docs/architecture.md` (request flow, ingestion, the versions part of storage);
  - `docs/adr/README.md`;
  - `docs/threat-model.md` (new upload endpoint in the upload and path traversal rows);
  - `docs/progress.md`;
  - the spec's status line.

**Steps:**
- [ ] Write the ADR (context, decision, consequences, rejected B and C) and the learning note.
- [ ] Run the end-to-end check with `docker compose up` and `examples/demo-shop`:
  1. upload `orders.csv`, approve, ask a question;
  2. add an `orders_dec.csv` with the same columns (made with `examples/generate.py` or by hand)
     and see it apply without review;
  3. add `orders.csv` again and see the overlap choice;
  4. add `products.csv` and see a proposed new table;
  5. check the divider and badges in the chat.
  Step 4 calls the real model once. Ask the user first and report the cost.
- [ ] Run `uv run ruff check .`, `uv run ruff format --check .`, `uv run pytest`,
  `uv run --env-file .env pytest tests/integration`, and `cd web && npm test && npm run build`.
  All of them pass.
- [ ] Commit: `docs: ADR 0045 dataset versions, learning note 24`.
