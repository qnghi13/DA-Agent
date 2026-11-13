# Result Export Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Download the full result of any query behind an answer as CSV, and any chart as PNG or SVG.

**Architecture:** The API enqueues an `export` job that names a run and a query index, never SQL.
The worker reads the stored SQL, re-checks it with the SQL guard, and asks the Runner for an
`ExportRun`: a sandbox with the dataset read-only at `/data` and an empty `/out`, which writes the
whole result as CSV or fails. The worker moves the file to `exports/`, and the API serves it to the
same workspace for 24 hours. Charts are exported in the browser from the Vega view.

**Tech Stack:** Python 3.12, DuckDB, FastAPI, Postgres job queue, React + Vite + vega-embed, pytest,
vitest.

**Spec:** [2026-11-14-result-export.md](2026-11-14-result-export.md)

## Global Constraints

- Rows: at most 1,000,000 (`MAX_EXPORT_ROWS = 1_000_000`). More fails the export; a cut file is never written.
- File size: at most 200 MB (`MAX_EXPORT_BYTES = 200 * 1024 * 1024`). Same rule.
- Time: 120 s in the sandbox (`EXPORT_TIMEOUT_SECONDS = 120`). Memory: the sandbox default, 512 MB.
- Kept for 24 hours (`EXPORT_TTL_SECONDS = 24 * 3600`), then deleted and no longer served.
- CSV: UTF-8 with BOM, comma, header row, ISO dates, no thousands separators, NULL as empty.
- Text cells starting with `=`, `+`, `-`, `@`, tab, or carriage return get a leading `'`. Numbers are never changed.
- File name: `da-agent-<first 8 chars of run id>-query-<n+1>.csv`; `n` in URLs is the 0-based index into `result.queries`.
- The endpoint takes indexes only, never SQL. The API never talks to the Runner.
- Refuse with 409 `"the dataset changed after this answer; ask again"` when the dataset is not `ready` or its `updated_at` is later than the run's `created_at`.
- Tests first (`tdd` skill). Agent-architecture skill before touching the sandbox or Runner (Task 2).
- Do not commit unless the user asks (CLAUDE.md overrides the commit steps below: stage, and report).

## Review Focus

1. **Text that looks like a formula or a number inside a text column** (`"-5"` as text, `"=1+1"`): escaped, while a real negative number stays `-5`. Test in Task 1.
2. **A query whose result is exactly 1,000,000 rows**: exported; 1,000,001 fails. Test in Task 1 with the cap lowered by parameter.
3. **An export asked for while the dataset is being rebuilt, or rebuilt between enqueue and run**: refused at both points. Tests in Tasks 4 and 5.
4. **Another workspace guessing an export id**: 404 on status and file. Test in Task 5.
5. **An expired or purged file whose job still says succeeded**: 404 `"the export expired; download it again"`, not 500. Test in Task 5.

---

### Task 1: CSV writer

**Files:**
- Create: `src/da_agent/storage/csv_export.py`
- Test: `tests/unit/test_csv_export.py`

**Interfaces:**
- Produces:
  - constants `MAX_EXPORT_ROWS`, `MAX_EXPORT_BYTES`, `EXPORT_TIMEOUT_SECONDS`, `EXPORT_TTL_SECONDS` (values above)
  - `class ExportReport(BaseModel)`: `rows: int`, `bytes: int`, `columns: list[str]`
  - `class ExportLimitError(ValueError)`: message for the user
  - `write_csv(database: Path, sql: str, out: Path, *, max_rows: int = MAX_EXPORT_ROWS, max_bytes: int = MAX_EXPORT_BYTES) -> ExportReport`

This runs inside the sandbox (imported from `/app`), so it uses only DuckDB and the standard library.

- [ ] **Step 1: Write the failing tests** (build a small DuckDB file in `tmp_path`)

```python
def test_writes_a_bom_header_and_every_row(tmp_path):  # 3 rows -> report.rows == 3
    # out.read_bytes().startswith(b"\xef\xbb\xbf"); header "id,name,amount"; Vietnamese "Hà Nội" round-trips
def test_dates_are_iso_and_numbers_have_no_separators(tmp_path):  # DATE '2026-06-01' -> "2026-06-01"; 22000 -> "22000"; NULL -> ""
def test_formula_text_is_escaped_but_numbers_are_not(tmp_path):
    # text "=1+1" -> "'=1+1"; "+84" -> "'+84"; "-5" (VARCHAR) -> "'-5"; "@x" -> "'@x"; "\tx" -> "'\tx"
    # INTEGER -5 -> "-5"
def test_exactly_the_row_cap_is_exported(tmp_path):  # 10 rows, max_rows=10 -> rows == 10
def test_over_the_row_cap_fails_and_leaves_no_file(tmp_path):
    # 11 rows, max_rows=10 -> ExportLimitError matching "more than 10 rows"; not out.exists()
def test_over_the_byte_cap_fails_and_leaves_no_file(tmp_path):  # max_bytes=50 -> ExportLimitError "over"; not out.exists()
def test_a_failing_query_leaves_no_file(tmp_path):  # SELECT * FROM missing -> duckdb.Error; not out.exists()
```

- [ ] **Step 2: Run** `uv run pytest tests/unit/test_csv_export.py -v`. Expected: FAIL, module not found.

- [ ] **Step 3: Implement `write_csv`.** Open the database `read_only=True`, `fetchmany` in batches of
  10,000 up to `max_rows + 1` rows, write with `csv.writer` to `out` opened with
  `encoding="utf-8-sig", newline=""`, track bytes written, and on any error or cap unlink `out`
  before re-raising. Escaping applies only to `str` values. `datetime.date`/`datetime` use
  `isoformat()`, `Decimal` uses `str`. Messages: `"the result has more than {max_rows:,} rows; add a filter or aggregate"`, `"the file is over {max_bytes // 1_048_576} MB; select fewer columns or rows"`.

- [ ] **Step 4: Run** the same command. Expected: PASS. Then `uv run ruff check src tests`.

- [ ] **Step 5: Stage** `git add src/da_agent/storage/csv_export.py tests/unit/test_csv_export.py`

---

### Task 2: Export in the sandbox and the Runner

Load the `agent-architecture` skill first.

**Files:**
- Modify: `sandbox/da_sandbox/__main__.py` (new `export` kind)
- Modify: `src/da_agent/runner/runner.py` (`ExportRun`, `ExportResult`, `Runner.export`, `build_command`, `_job`, `SandboxRunner`)
- Modify: `src/da_agent/runner/service.py` (`POST /v1/export`)
- Modify: `src/da_agent/runner/client.py` (`RunnerClient.export`)
- Test: `tests/unit/test_runner.py`, `tests/unit/test_runner_service.py`, `tests/security/test_sandbox_export.py`

**Interfaces:**
- Consumes: `write_csv`, `ExportReport`, `EXPORT_TIMEOUT_SECONDS`, `MAX_EXPORT_BYTES` from Task 1.
- Produces:
  - `class ExportRun(BaseModel)`: `kind: Literal["export"] = "export"`, `dataset_id` (same pattern as `SqlRun`), `sql: str = Field(max_length=MAX_SQL_CHARS)`, `timeout_seconds: int = Field(default=EXPORT_TIMEOUT_SECONDS, ge=1, le=EXPORT_TIMEOUT_SECONDS)`; `extra="forbid"`, frozen
  - `ExportResult = JobResult[ExportReport]`; on success `output_dir/"result.csv"` and `output_dir/"export_report.json"` exist
  - `SandboxRunner.export(request: ExportRun) -> ExportResult`, implemented by `Runner` and `RunnerClient`

- [ ] **Step 1: Write the failing tests**

```python
# test_runner.py
def test_an_export_mounts_the_dataset_read_only_and_a_fresh_out():
    # build_command(ExportRun(...), name, out_dir) contains "/data" with readonly and "/out" without; "--network=none"
def test_an_export_cannot_name_a_dataset_outside_the_root():  # dataset_id "../x" -> ValidationError
def test_an_export_output_over_the_byte_cap_is_rejected(tmp_path):  # fake executor writes > MAX_EXPORT_BYTES + 1 MB -> status "error", output_dir None
# test_runner_service.py
def test_an_export_goes_through_the_service(...)  # mirrors test_a_query_goes_through_the_service
def test_an_export_output_outside_staging_is_refused(...)  # client rejects, as for build
# security/test_sandbox_export.py (Docker; skipped without it, same fixtures as test_sandbox_sql.py)
def test_an_export_writes_every_row(runner):  # 2,000-row table -> report.rows == 2000, csv has 2001 lines
def test_an_export_sees_only_its_dataset(runner):  # reading shop_2 path or CANARY from shop_1 fails, canary absent from out
```

- [ ] **Step 2: Run** `uv run pytest tests/unit/test_runner.py tests/unit/test_runner_service.py tests/security/test_sandbox_export.py -v`. Expected: FAIL.

- [ ] **Step 3: Implement.** Sandbox: `run_export(request)` calls `write_csv(Path("/data/dataset.duckdb"), request["sql"], Path("/out/result.csv"))` and writes `/out/export_report.json`; an `ExportLimitError` or `duckdb.Error` prints a one-line message to stdout as `{"error": ...}` and exits 1. Runner: `build_command` gives `ExportRun` the dataset mount at `/data` (read-only) plus `/out`; `_job` accepts `ExportRun` (its input check is `_child(datasets_root, ...)`); `export()` = `_job(request, "export_report.json", ExportReport)` with the output-size check using `MAX_EXPORT_BYTES + 1_048_576` for exports. On failure, put the sandbox's one-line error in `stderr` so the worker can show it. Service and client follow `build` exactly.

- [ ] **Step 4: Run** the same command. Expected: PASS (the security file passes when Docker is up; otherwise SKIP). Rebuild the sandbox image if the test fixture does not.

- [ ] **Step 5: Stage** the files above.

---

### Task 3: Storage: job kind and export files

**Files:**
- Create: `src/da_agent/storage/migrations/0010_export_jobs.sql`
- Create: `src/da_agent/storage/exports.py`
- Modify: `src/da_agent/storage/jobs.py:23` (`JobKind` adds `"export"`)
- Modify: `src/da_agent/config.py` (`Settings.exports_root` = `data_dir / "exports"`)
- Test: `tests/integration/test_exports_storage.py`, `tests/unit/test_config.py`

**Interfaces:**
- Produces:
  - migration: `jobs_kind_check` allows `('ingest', 'build', 'run', 'export')`
  - `export_file(root: Path, job_id: UUID) -> Path` (`root / f"{job_id}.csv"`)
  - `export_filename(run_id: str, query_index: int) -> str`
  - `dataset_changed_since_run(conn, workspace_id: UUID, run_id: UUID, dataset_id: UUID) -> bool`: true when the dataset is missing, not `ready`, or `updated_at > ` the run job's `created_at`
  - `purge_expired(root: Path, *, now: float, ttl_seconds: int = EXPORT_TTL_SECONDS) -> int`: deletes `*.csv` older than the TTL by mtime, returns how many
  - `is_expired(path: Path, *, now: float, ttl_seconds: int = EXPORT_TTL_SECONDS) -> bool`

- [ ] **Step 1: Write the failing tests**

```python
def test_an_export_job_can_be_queued(conn, workspace):  # JobQueue(conn).enqueue(ws, "export", {...}) succeeds
def test_a_dataset_rebuilt_after_the_run_counts_as_changed(conn, ...):
    # run created, then UPDATE datasets SET updated_at = now() + interval '1 s' -> True; before -> False
def test_a_dataset_that_is_not_ready_counts_as_changed(conn, ...):  # status 'building' -> True
def test_old_exports_are_purged_and_new_ones_kept(tmp_path):  # os.utime 25 h ago -> deleted; 1 h ago -> kept; returns 1
def test_export_filename():  # export_filename("1234abcd-...", 0) == "da-agent-1234abcd-query-1.csv"
```

- [ ] **Step 2: Run** `uv run --env-file .env pytest tests/integration/test_exports_storage.py tests/unit/test_config.py -v`. Expected: FAIL.

- [ ] **Step 3: Implement** the migration (drop and re-add the check, as `0002_datasets.sql` does) and the functions.

- [ ] **Step 4: Run** the same command. Expected: PASS.

- [ ] **Step 5: Stage** the files above.

---

### Task 4: Worker export handler

**Files:**
- Create: `src/da_agent/worker/exports.py`
- Modify: `src/da_agent/worker/__main__.py` (register `"export"`, create `exports_root`)
- Test: `tests/integration/test_worker_exports.py`

**Interfaces:**
- Consumes: `ExportRun`, `SandboxRunner.export` (Task 2); `export_file`, `export_filename`, `dataset_changed_since_run`, `purge_expired` (Task 3); `validate_select(sql, dialect="duckdb")`; `PermanentJobError`, `RetryableJobError`.
- Produces: `class ExportHandler` with `__init__(self, *, url: str, schema: str | None, runner: SandboxRunner, exports_root: Path)` and `__call__(self, job: Job, lost: threading.Event) -> dict[str, Any]` returning `{"rows": int, "bytes": int, "filename": str}`. Job payload: `{"run_id": str, "query_index": int, "dataset_id": str}`.

- [ ] **Step 1: Write the failing tests** (fake runner that writes a CSV into a staging dir)

```python
def test_an_export_runs_the_stored_sql_and_keeps_the_file(...):
    # fake runner received ExportRun.sql == run result queries[1].sql; file at export_file(root, job.id); result rows/bytes/filename
def test_sql_the_guard_rejects_is_never_run(...):  # stored sql "DROP TABLE x" -> PermanentJobError, runner not called
def test_a_dataset_changed_after_the_run_fails_the_export(...):  # PermanentJobError "the dataset changed after this answer; ask again"
def test_a_limit_error_from_the_sandbox_is_shown(...):  # runner status error, stderr "the result has more than ..." -> PermanentJobError with that text
def test_an_unavailable_runner_is_retried(...):  # stderr UNAVAILABLE -> RetryableJobError
def test_expired_exports_are_purged_on_each_export(...)
```

- [ ] **Step 2: Run** `uv run --env-file .env pytest tests/integration/test_worker_exports.py -v`. Expected: FAIL.

- [ ] **Step 3: Implement.** Read the run with `JobQueue.get(run_id, job.workspace_id)` and its `result["queries"][query_index]["sql"]`; a missing run, index, or a dataset id that differs from the run's payload is `PermanentJobError("not found")`. Move `output_dir / "result.csv"` with `shutil.move` and remove the staging dir in `finally`.

- [ ] **Step 4: Run** the same command. Expected: PASS.

- [ ] **Step 5: Stage** the files above.

---

### Task 5: API endpoints

**Files:**
- Modify: `src/da_agent/api/app.py` (`ApiSettings.exports_root`; three routes)
- Modify: `src/da_agent/api/__main__.py` (pass `exports_root`)
- Test: `tests/integration/test_api_exports.py`

**Interfaces:**
- Consumes: Task 3 functions, `run_job`, `_may_start_job`, `log`.
- Produces:
  - `POST /v1/runs/{run_id}/queries/{n}/export` → 202 `{"id", "status": "queued"}`; 409 when the run is not succeeded or the dataset changed; 404 for an unknown run or index
  - `GET /v1/exports/{export_id}` → `{"id", "status", "rows", "bytes", "filename", "error"}` (job kind must be `export`)
  - `GET /v1/exports/{export_id}/file` → `text/csv; charset=utf-8`, `Content-Disposition: attachment; filename="<filename>"`, `X-Content-Type-Options: nosniff`; 409 if not succeeded; 404 `"the export expired; download it again"` when the file is missing or `is_expired`
  - audit actions `export.create`, `export.download`

- [ ] **Step 1: Write the failing tests**

```python
def test_an_export_of_a_finished_run_is_queued_and_logged(...)  # 202; job kind "export", payload has run_id, query_index, dataset_id; audit export.create
def test_the_request_cannot_carry_sql(...)  # body {"sql": "..."} is ignored or 422; payload has no "sql"
def test_an_unknown_index_is_404(...)  # n = len(queries) -> 404
def test_an_unfinished_run_is_409(...)
def test_a_changed_dataset_is_409(...)  # detail "the dataset changed after this answer; ask again"
def test_a_finished_export_downloads_as_csv(...)  # headers as above; body bytes equal the file; audit export.download
def test_another_workspace_gets_404_for_status_and_file(...)
def test_an_expired_export_is_404_not_500(...)  # file mtime 25 h ago, or file deleted
```

- [ ] **Step 2: Run** `uv run --env-file .env pytest tests/integration/test_api_exports.py -v`. Expected: FAIL.

- [ ] **Step 3: Implement** the routes; serve the file with `FileResponse`. Check `n` against `len(job.result["queries"])`.

- [ ] **Step 4: Run** the same command, then `uv run pytest` and `uv run ruff check .`. Expected: all PASS.

- [ ] **Step 5: Stage** the files above.

---

### Task 6: Web: download CSV and chart images

**Files:**
- Modify: `web/src/api.ts` (`startExport`, `getExport`, `exportFileUrl`)
- Create: `web/src/export.ts` (`waitForExport`), `web/src/export.test.ts`
- Modify: `web/src/components/chat/evidence.tsx` (`QueryRows`: Download CSV button)
- Modify: `web/src/Chart.tsx` (menu with PNG and SVG)

**Interfaces:**
- Consumes: Task 5 routes.
- Produces:
  - `type ExportJob = { id: string; status: "queued" | "running" | "succeeded" | "failed"; rows: number | null; bytes: number | null; filename: string | null; error: string | null }`
  - `api.startExport(runId: string, index: number): Promise<{ id: string }>`; `api.getExport(id: string): Promise<ExportJob>`; `exportFileUrl(id: string): string` (`/v1/exports/<id>/file`)
  - `waitForExport(get: (id: string) => Promise<ExportJob>, id: string, opts?: { intervalMs?: number; timeoutMs?: number; sleep?: (ms: number) => Promise<void> }): Promise<ExportJob>`: polls every 1,000 ms until `succeeded` or `failed`, rejects after 180,000 ms

- [ ] **Step 1: Write the failing tests** in `export.test.ts`

```ts
test("resolves when the export succeeds")  // queued, running, succeeded -> resolves with the last job
test("resolves with the error when it fails")  // failed + error text -> resolves; caller shows error
test("gives up after the timeout")  // always running, fake sleep -> rejects "the export is taking too long"
```

- [ ] **Step 2: Run** `cd web && npm test -- export`. Expected: FAIL.

- [ ] **Step 3: Implement.** In `QueryRows`, a ghost button "Download CSV" (lucide `Download`), label "Preparing file…" while waiting, then navigate an `<a download>` to `exportFileUrl`; show `error` in `text-danger` below. In `Chart`, keep the embed `result.view`; a small dropdown (existing `dropdown-menu`) with "Download PNG" (`view.toImageURL("png", 2)`) and "Download SVG" (`view.toImageURL("svg")`), file name from the chart title slug or `chart`.

- [ ] **Step 4: Run** `cd web && npm test && npm run typecheck`. Expected: PASS. Then check in the browser with the `run` skill on `examples/demo-shop`: download a query CSV and a chart PNG.

- [ ] **Step 5: Stage** the files above.

---

### Task 7: Docs

**Files:**
- Create: `docs/adr/0032-result-export.md`, `docs/learning/12-xuat-ket-qua.md`
- Modify: `docs/adr/README.md`, `docs/architecture.md` (Output, Module map), `docs/threat-model.md` (export endpoint, `/out` mount, CSV injection), `docs/learning/README.md`, `docs/progress.md`, `README.md` (one line under How it works)

- [ ] **Step 1:** ADR 0032 from the spec's Approaches, Limits, and Security sections. Learning note in Vietnamese: display vs download limits, prior art, export job vs API-runner, CSV injection, likely interview questions.
- [ ] **Step 2:** `uv run pytest && uv run ruff check . && (cd web && npm test && npm run typecheck)`. Expected: all PASS.
- [ ] **Step 3: Stage** the docs and report to the user; commit only when asked.
