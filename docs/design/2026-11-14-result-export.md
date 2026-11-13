# Result export: design

**Status:** implemented, 2026-11-14 ([ADR 0032](../adr/0032-result-export.md)).

## Intent

A data analyst takes results out of DA-Agent and keeps working on them elsewhere: a table into
Excel, a chart into a slide. Today they can copy the SQL, copy the answer, and download the HTML
report. They cannot download a query's rows or a chart.

The rule the user set: **a download holds the whole result, or the download does not exist.** A
file that silently holds only the first rows misleads, so it is worse than no file.

## Goals

- Download the full result of any successful query behind an answer, as CSV.
- Download any chart of an answer as PNG or SVG.
- Keep every security boundary that exists today.

## Non-goals

- XLSX, Parquet, or JSON. CSV opens in every tool; add others when someone needs them.
- Exporting SQL the user typed or edited. Only queries the agent already ran are exported.
- Scheduled or emailed exports, sharing links for people outside the workspace.

## Anti-goals (a failure even if it works)

- A file with fewer rows than the query returns, without saying so.
- The API holding the runner token, or any path that runs SQL outside the sandbox.
- An endpoint that accepts SQL text from the client.

## Constraints found in the code

- A stored query result holds at most 50 rows (`max_rows=50` in `storage/duckdb_query.py` and
  `storage/sqlite_dataset.py`; `SqlRun.max_rows` is capped at 500). A full export must run the
  query again.
- Only the worker reaches the runner (`compose.yml`, ADR 0024). The API never runs SQL.
- Sandbox SQL results come back as JSON on stdout, capped at `max_output_bytes` (100 KB). A full
  result cannot travel that way; it must be written to a mounted output directory, as ingest and
  build already do (`/out`).
- Charts already hold every row they plot: `make_chart` refuses a truncated result
  (`agent/charts.py`). So a chart image is always complete.
- Hidden (sensitive) columns are not in the clean dataset (`ingestion/build.py`), so a query on it
  cannot export them.

## Prior art

Metabase, Looker, Superset, and BigQuery separate the display limit from the download limit: a
download re-runs the saved query with a much higher cap (Metabase 1 million rows by default,
Looker 100,000 unstreamed). Tools whose app server reads the database directly stream the file in
the request. Where the query runs elsewhere, as in Superset's Celery workers or BigQuery's
`EXPORT DATA`, the export is a background job that writes a file, then the user downloads it.

## Approaches

| | How | Trade-off |
|---|---|---|
| **A. Export job (chosen)** | API enqueues an `export` job; the worker runs the query in a sandbox that writes a CSV; the UI waits, then downloads | Keeps the boundary; reuses queue, limits, audit, traces. A few seconds of sandbox start-up |
| B. API calls the runner | API re-runs the query and streams the file in the request | Simplest UX, but gives the API the runner token and holds requests open on large files |
| C. Export at run time | The worker writes a full CSV for every query while answering | Wasted work for queries nobody downloads; slower answers |

The user chose A on 2026-11-14 after comparing with production systems.

## Design

### Flow

1. The user clicks **Download CSV** on query *n* of a run.
2. `POST /runs/{run_id}/queries/{n}/export` checks the run belongs to the caller's workspace,
   succeeded, and has a query *n* that succeeded; checks the dataset is still ready; applies the
   same job limits as a run; enqueues an `export` job with `{run_id, query_index, dataset_id}`;
   writes an audit entry. It returns `{id, status}`. The SQL is read from the stored run result by
   the worker, never from the request.
3. The worker's `export` handler reads the SQL from the run result, checks it again with the SQL
   guard, and asks the runner for an `ExportRun`.
4. The sandbox mounts the dataset read-only at `/data` and an empty output directory at `/out`,
   runs the query in DuckDB, and writes `/out/result.csv`. It stops with an error if the result
   has more than the row cap or the file would pass the byte cap; it never writes a cut file.
5. The worker moves the file to `exports/<job id>.csv` on the shared volume and completes the job
   with `{rows, bytes, filename}`.
6. The UI follows the job (events or polling, as for runs), then downloads
   `GET /exports/{job_id}/file`, which checks the workspace and serves the file as an attachment.

### Limits

| Limit | Value | Why |
|---|---|---|
| Rows | 1,000,000 | Metabase's default; Excel's sheet limit is 1,048,576 |
| File size | 200 MB | Bounds the volume and the download |
| Time | 120 s | Bigger than a question query (15 s), still bounded |
| Memory | the sandbox default (512 MB) | DuckDB writes CSV in a streaming way |
| Kept for | 24 hours, then deleted | Exports are copies of user data; do not keep them |

Over a limit, the job fails with a message the user can act on, for example "the result has more
than 1,000,000 rows; add a filter or aggregate".

### File format

CSV, UTF-8 with a byte-order mark so Excel shows Vietnamese correctly, comma separator, header
row, ISO dates, numbers without thousands separators. File name:
`da-agent-<run id prefix>-query-<n>.csv`.

### Charts

In the browser only: a menu on each chart with **PNG** and **SVG**, using the Vega view's
`toImageURL`. No server work, since the chart already holds every row.

### Security

- The export endpoint takes indexes, never SQL. The worker re-checks the stored SQL with the SQL
  guard before running it.
- The sandbox keeps every current restriction (no network, read-only root, one dataset). The only
  new mount is a fresh, empty, writable `/out`.
- The download checks the caller's workspace; another workspace gets 404.
- The served file is `text/csv` with `Content-Disposition: attachment` and `nosniff`.
- CSV injection: cells starting with `=`, `+`, `-`, `@` can run as formulas in Excel. Prefix such
  text cells with `'` (OWASP guidance). Numbers stay numbers.

### Changes by part

- **Sandbox** (`sandbox/da_sandbox/__main__.py`): an `export` request kind.
- **Runner** (`runner/runner.py`, service, client): `ExportRun` and `export()`, mounting `/out`.
- **Storage**: migration adding `export` to the job kinds; an exports directory on the volume.
- **Worker**: an `export` handler; deletion of exports older than 24 hours.
- **API**: the two endpoints above; audit entries `export.create` and `export.download`.
- **Web**: a Download CSV button per query in the Data tab with a waiting state; a chart menu.
- **Docs**: ADR 0032, architecture and threat model updates, a learning note.

### Data at export time

The export runs on the dataset as it is now. An edited spec rebuilds the dataset under the same id
(ADR 0019), so the data can differ from what the agent saw. The export is therefore refused, with
409 and "the dataset changed after this answer; ask again", when the dataset is not ready or its
`updated_at` is later than the run's `created_at`. The API checks this when the export is asked
for, and the worker checks again before running it.

## Left to the builder

- Whether the UI follows the export job by SSE or by polling `GET /runs/{id}`-style status.
- Whether DuckDB `COPY` or Python's `csv` writes the file, as long as the caps and formula
  escaping hold and the file is never cut.
- Reusing a finished export of the same query within its 24 hours instead of running it again.
