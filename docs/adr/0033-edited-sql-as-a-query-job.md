# 0033. Edited SQL runs as a query job, checked before it is queued

**Status:** Accepted (2026-11-14). Extends [0011](0011-sql-per-request-sandbox.md),
[0024](0024-runner-service.md), and [0032](0032-result-export.md). Design:
[2026-11-14-edit-sql.md](../design/2026-11-14-edit-sql.md).

**Context:** Analysts check a number by changing the query behind it: a filter, a column, a
grouping. Asking the model again costs money and may change more than the one thing. Databricks
Genie, Snowflake Cortex Analyst, and Metabase show generated SQL and let people who know SQL edit
it. Until now only the model wrote SQL; this adds SQL typed by a person, which may be a typo,
plain text, or an attack. Only the worker reaches the Runner.

**Decision:**
- `POST /v1/runs/{id}/queries` takes `{sql}` (at most 20,000 characters) for a succeeded run whose
  dataset is ready. The API runs the SQL guard first: a parse error, a non-`SELECT`, several
  statements, or a file read is a 422 with the guard's message, and nothing is queued. Parse
  messages lose sqlglot's terminal codes, which also cleans what the model sees.
- A valid query becomes a `query` job. The worker checks it again, runs it with `Runner.query`
  (50 rows, 15 s, the locked DuckDB engine), and stores `{sql, columns, rows, truncated}`. A DuckDB
  error fails the job with DuckDB's message; "runner unavailable" is retried; time and memory
  limits fail with fixed messages.
- `POST /v1/queries/{id}/export` exports the whole result through the existing export job, which
  now names either a run's query or a query job and always reads SQL from the stored result. The
  changed-dataset rule counts from the query job's time.
- Query jobs and their exports take the job rate, not the daily model budget: they call no model.
  The audit log records `query.create` with the SQL's length, not its text.

**Consequences:**
- The boundary is unchanged: the API holds no runner token, and person-written SQL takes the same
  path as model-written SQL, which was never trusted.
- Every run of edited SQL starts a sandbox: a few seconds, bounded by the job rate.
- Edited queries are not saved or fed to memory; a page reload loses the draft.
