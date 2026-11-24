# Delete a dataset

**Status:** implemented and merged into `main` (2026-11-24); not yet checked
in the running app. Recorded in [ADR 0050](../adr/0050-dataset-deletion.md). The user chose a
background delete job (approach A).

## Why

A user must be able to remove their data from the system. Today nothing deletes a dataset: no API
route, no UI. ADR 0004 assumed "deleting a dataset means deleting a file", but a dataset's data now
lives in many places, and answers, reports, and checkpoints hold values read from it.

The user decided (2026-11-24):

- The purpose is the user removing **their own data**, so the history goes too: every run,
  answer, report, edited query, export, conversation, note, and metric of the dataset.
- Build approach A: the API marks the dataset `deleting` and queues a `delete` job; the worker
  stops the dataset's jobs, removes its files, then its rows.

## Goals, non-goals, anti-goals

**Goals.**

- `DELETE /v1/datasets/{id}` deletes a dataset of the caller's workspace, whatever its status.
- From the moment the API answers, the dataset and everything of it are gone for the user: not
  listed, every read returns 404, nothing new can start on it.
- When the `delete` job succeeds, nothing that holds the dataset's data is left in Postgres or on
  disk (inventory below), and a crash at any point is finished by a retry.
- A delete button in the web UI, in English and Vietnamese, with a confirmation that says the
  history goes too.

**Non-goals.**

- Undo, a trash bin, or a grace period. Deletion is final.
- Phoenix traces and container logs. Traces hold prompts and query results; they expire with
  Phoenix's own retention.
- Golden rerun reports (`data_dir/goldens/goldens-<time>.jsonl`, written by the operator's
  `python -m da_agent.goldens run`). One file mixes datasets and nothing in the database points to
  it; the operator owns these files. The golden cases themselves are deleted.
- Both gaps are written down in the threat model (residual risks) and the README, not fixed.
- Deleting a whole workspace or account.
- Operator tools: no admin command or UI for deletion. A user retries a failed delete by asking
  again (see the API).

**Anti-goals** (a failure even if the endpoint works):

- Any copy of the data left behind after the job succeeds: an export CSV, an old version's build,
  a checkpoint, a run's answer in `jobs.result`.
- A job that keeps reading the data, or a new job that starts on it, after the user deleted it.
- Deletion blocked by the daily budget, rate limits, or the job-slot limit. Removing data must
  always be possible.
- Spend records lost: the daily budget and cost reports must stay correct.

## Inventory: what holds the dataset's data

Postgres (`d` = the dataset id; every job of a dataset has `payload ->> 'dataset_id' = d`, for all
kinds: `ingest`, `build`, `version`, `run`, `query`, `export`):

| Data | Where | How it goes |
|------|-------|-------------|
| The dataset, spec, summary, documents | `datasets` | deleted |
| Versions | `dataset_versions` | cascade from `datasets` |
| Notes, metrics, conversations | `dataset_memories`, `conversations` | cascade from `datasets` |
| Jobs: questions, answers, SQL, results | `jobs` (payload, result, last_error) | deleted |
| Progress events | `run_events` | cascade from `jobs` |
| Attempts | `job_attempts` | cascade from `jobs` |
| Feedback, golden cases | `run_feedback`, `golden_cases` | cascade from `jobs`; `golden_cases` also deleted by `dataset_id` |
| Agent state of each run | LangGraph checkpoint tables, thread id = run job id | deleted per thread |
| Time travel forks | checkpoint threads `fork-<run id>-<hex>` | deleted per thread |

Kept on purpose: `llm_calls` (tokens and cost only, keyed by `run_id` text, no FK; the daily
budget sums it), `audit_log` (actions and ids, never data values; gains a `dataset.delete` row),
and the `delete` job's own row (payload holds only the dataset id).

Disk:

| Data | Where |
|------|-------|
| Uploaded files, every version | `uploads_root/<upload_dir>` for each version row: `d` and `d-v<N>-<hex>` |
| Builds (DuckDB and its files) | `datasets_root/<build_dir>` for each version row, and `d.old` |
| Export CSVs | `exports_root/<export job id>.csv` for each export job of the dataset |
| Parsed uploads | `staging_root/da-sbx-*`, left by a successful ingest or build (a bug, below) |

The worker also removes any directory in `uploads_root` or `datasets_root` whose name is `d`,
`d.old` (left when `_install` crashes between its two renames), or starts with `d-v`, to catch
folders whose row says nothing (a crash between creating the folder and writing the row).

**Staging leak (an existing bug, fixed in this work).** `prepare_dataset` and `rebuild_dataset`
never remove the ingest output after a successful ingest or build, and nothing links that folder
to its dataset, so a delete cannot find it. Two parts:

- The ingest and build handlers remove their staging output when they finish, success or not, as
  `VersionHandler` already does.
- Staging is scratch space by definition: the worker, at start and in every `delete` job, removes
  `staging_root` folders older than 24 h (far beyond any job's deadline). This also clears the
  folders already left by earlier ingests. My call: it is the only way to reach old folders,
  since nothing records which dataset they belong to.

## The design

### States

`datasets.status` gains `deleting`. It is set once, by the API, and never left: the row is removed
at the end. `jobs.kind` gains `delete`. One migration adds both.

Today the ingest and build handlers write `datasets.status` unconditionally when they finish
(`DatasetStore.mark_proposed`, `mark_ready`, `mark_failed`). These writes, and every other write
to a dataset or its versions by a job (`DatasetVersionStore` steps, version apply), get a guard
(`AND status <> 'deleting'` on the dataset) so a job that finishes during a delete cannot bring
the dataset back. A job whose write is refused this way ends without error.

### API: `DELETE /v1/datasets/{id}`

In one transaction:

1. Lock the dataset row (`FOR UPDATE`) in the caller's workspace. None: 404. Already `deleting`
   with an open (`queued` or `running`) `delete` job: answer 202 again, change nothing. Already
   `deleting` with no open `delete` job (the last one failed): queue a new one, so the user can
   always retry by asking again.
2. Set `status = 'deleting'`.
3. Request cancel on every `queued` or `running` job of the dataset (`JobQueue.request_cancel`
   semantics: a queued job ends `cancelled` at once and is never claimed; a running one gets
   `cancel_requested_at`).
4. Enqueue one `delete` job with payload `{"dataset_id": d}`.
5. Audit `dataset.delete`.

Answer `202 {"id": d, "status": "deleting"}`. It does not call `_may_start_job`: no budget, rate,
or slot check (anti-goal).

### Hiding a dataset that is being deleted

Every API read treats a `deleting` dataset as absent (404), and so every job of it: the dataset
list, the dataset view, versions, memory, runs list and run view, run events, report, queries,
exports and their files, feedback, conversations. Every write that starts a job or changes the
dataset (new run, add files, approve, discard, export, edited query, memory changes, metrics)
also gets 404.

Closing the race with a request that read the dataset just before the delete committed: every
endpoint that enqueues a job for a dataset locks the dataset row with `FOR SHARE` in the same
transaction as the enqueue and checks that it is not `deleting`. The delete takes `FOR UPDATE`, so
one waits for the other, and a job enqueued first is then seen and cancelled by step 3.

### Worker: the `delete` job

The handler, for dataset `d` (a missing dataset row means an earlier attempt finished the rows:
succeed):

1. **Stop.** Request cancel again on every open job of `d` except itself. If any is still
   `running`, wait: the job is put back in the queue for a later try, and that wait does **not**
   use up an attempt. A run stops within its current model call or query (ADR 0042); other job
   kinds do not check for cancel and stop when they finish, bounded by their own deadlines
   (sandbox calls up to 30 min, plus model calls). A cancelled job is never claimed again, and a
   job whose worker died ends through the existing lease expiry (`fail_expired`), so the wait
   always ends. As a safety net, a delete that has waited more than 2 h ends `failed`: it shows in
   ops failures, the data stays hidden, and the user's next `DELETE` queues a new one. Files are
   never removed under a running job.
2. **Files.** Remove the export CSVs of the dataset's export jobs, then every upload and build
   folder listed above. A missing file or folder is fine. Paths are built only from ids read from
   the database and checked to be direct children of their root.
3. **Checkpoints.** Delete the checkpoint thread of every run job of `d`, and every fork thread
   whose id starts with `fork-<run id>-`.
4. **Rows**, in one transaction: delete `golden_cases` of `d`, every job of `d` except the delete
   job itself, then the `datasets` row (cascades do the rest).

Files and checkpoints go before rows, so if the worker dies after step 2 or 3 the rows still say
what to delete and the retry repeats the steps harmlessly. Every step is idempotent.

On its last attempt failing, the job ends `failed` as usual (it shows in ops failures and the job
failure SLO); the dataset stays `deleting` and hidden, and a new `DELETE` from the user retries.

### Web UI

- A "Delete dataset" action in the open dataset's view, available in every status.
- Confirmation with `window.confirm`, as the memory panel does: "Delete <name>? Its files, every
  answer and chat about it, notes, and metrics are deleted for good." (Vietnamese in `vi.ts`).
- On 202 the dataset leaves the list at once and the view returns to upload. An error shows the
  API error by `code` like the other actions.

### Records

- ADR 0050: dataset deletion as a background job, and what is kept (spend, audit) and not
  covered (Phoenix traces).
- ADR 0004 gets a pointer to 0050. The threat model lists deletion under threats and controls, and
  the Phoenix traces and golden reports under residual risks.

## Consequences accepted

- Ops numbers computed from `jobs` (job outcomes, latency SLOs over a window) lose the deleted
  jobs, so a past window can change after a deletion. Prometheus counters are not affected.
- A run being deleted may spend a little more while it finishes its current model call.

## Left to the builder

- How the delete job waits without using an attempt (for example a dedicated `JobQueue` method
  that puts a running job back as `queued` with a later `run_after` and gives the attempt back),
  and the retry delay.
- How the hide rule is put into the existing lookups (`DatasetStore.get` and `in_workspace`, and
  the run/query/export job getters), as long as every route listed above is covered by a test.
- Exact UI placement and wording within the above.
- Whether to delete checkpoint threads through `PostgresSaver.delete_thread` or SQL on its tables.

## Tests

Postgres tests (`uv run --env-file .env pytest`): the full inventory is gone after the job, with
a fixture that creates every kind of row and file; a second attempt after a crash at each step;
a running job blocks the job without spending attempts; an ingest or build that finishes during
the delete does not bring the dataset back; a second `DELETE` after a failed delete queues a new
job; ingest and build leave no staging folder; old staging folders are purged; a run created during the delete is
cancelled or refused; every listed route returns 404 while `deleting`; another workspace's
dataset returns 404 and is untouched; `llm_calls` and audit rows survive. Web: the button, the
confirmation, and the list update.
