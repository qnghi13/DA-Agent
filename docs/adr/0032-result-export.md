# 0032. Result export is a job that writes the whole result in a sandbox

**Status:** Accepted (2026-11-14). Extends [0011](0011-sql-per-request-sandbox.md),
[0015](0015-charts-built-by-code.md), and [0024](0024-runner-service.md). Design:
[2026-11-14-result-export.md](../design/2026-11-14-result-export.md).

**Context:** Analysts take results into Excel and slides. A run stores at most 50 rows per query,
which is enough for the model and the preview but not for a download, and a file holding only the
first rows misleads. Only the worker reaches the Runner; the API never runs SQL. Metabase and
Looker re-run the saved query with a download cap far above the display cap; where the query runs
elsewhere, as in Superset's Celery workers or BigQuery's `EXPORT DATA`, the export is a background
job that writes a file.

**Decision:**
- `POST /v1/runs/{id}/queries/{n}/export` enqueues an `export` job naming the run and the query
  index. The request never carries SQL. The worker reads the stored SQL, checks it again with the
  SQL guard, and asks the Runner for an `ExportRun`.
- The sandbox mounts the dataset read-only at `/data` and a fresh `/out`, opens DuckDB with the
  locked engine configuration, and writes the whole result as CSV (UTF-8 with BOM) or no file.
  Caps: 1,000,000 rows, 200 MB, 120 s. A cap or query error comes back in the report's `error`,
  in words for the user; sandbox stderr is never shown.
- Text cells starting with `=`, `+`, `-`, `@`, tab, or carriage return get a leading `'`.
- The worker moves the file to `exports/<job id>.csv`; the API serves it to the same workspace
  for 24 hours and then refuses it as expired. Each export deletes older files.
- An edited spec rebuilds a dataset under the same id (ADR 0019), so an export is refused when the
  dataset is not ready or changed after the run, both when it is asked for and before it runs.
- Charts are exported in the browser as PNG or SVG from the Vega view, with the theme's surface
  as background. `make_chart` refuses truncated results, so a chart holds every row it plots.

**Consequences:**
- The API still holds no runner token and never runs SQL. Exports share the job queue's rate
  (not the daily model budget: they call no model), audit (`export.create`, `export.download`), retries, and traces (`job.export`).
- A download waits a few seconds for a sandbox to start.
- Exports are copies of user data on the shared volume for up to a day.

**Rejected:**
- The API calling the Runner and streaming the file: the simplest experience, but the API would
  hold the runner token and keep requests open on large files.
- Writing every query's full result while the agent answers: work for files nobody downloads,
  and slower answers.
- Downloading the stored 50 rows: a file that looks complete and is not.
