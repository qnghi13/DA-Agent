# 0050: A dataset is deleted by a background job; the API only hides it first

**Status:** Accepted, 2026-11-24.

**Context:** A user must be able to remove their own data. Nothing deleted a dataset: no route and
no UI. ADR 0004 assumed "deleting a dataset means deleting a file", but a dataset's data now lives
in many places: uploads of every version, builds, export CSVs, the dataset row and its versions,
notes, metrics and conversations, every job (questions, answers, SQL, results), progress events,
feedback, golden cases, and the LangGraph checkpoints of each run and its time-travel forks. The
user decided the history goes too. Deletion must also never be blocked by the daily budget, rate
limits, or the job-slot limit, and must not lose the spend records.

**Decision:**
- **Hide first, delete in the background.** `DELETE /v1/datasets/{id}` runs one transaction: lock
  the dataset row, set `status = 'deleting'`, request cancel on every open job of the dataset,
  enqueue one `delete` job, and audit `dataset.delete`. It answers 202 and skips the budget, rate,
  and slot checks. A `deleting` dataset and all its jobs are "not found" on every route. Every
  endpoint that enqueues a job for a dataset locks the row `FOR SHARE` and refuses a `deleting` one,
  so no job can start after the delete committed. Writes by finishing jobs are guarded
  (`status <> 'deleting'`), so a late ingest or build cannot bring the dataset back. A second
  `DELETE` is a retry: 202 again while a delete job is open, a new job if the last one failed.
- **The `delete` job** (`DeleteHandler`) does, in order: stop the dataset's other jobs, remove
  files (exports, uploads, builds, folders named for the dataset that no row mentions), purge old
  staging, delete checkpoint threads (runs and `fork-<run id>-` forks), then delete rows in one
  transaction. Files and checkpoints go before rows, so a crash leaves rows that say what is left
  and the retry repeats each idempotent step. Paths are built only from database ids and must be
  direct children of their root.
- **Waiting spends no attempt.** While another job of the dataset still runs, the handler raises
  `JobWaitError`: the job returns to the queue with a later `run_after`, and the attempt is given
  back (attempt result `wait`, not a failure). Files are never removed under a running job. A
  delete whose own age passes 2 h ends `failed`, so it shows in ops failures; the data stays hidden
  and the user's next `DELETE` queues a new job.
- **Kept on purpose:** `llm_calls` (tokens and cost, no data values; the daily budget sums it),
  `audit_log`, and the delete job's own row (its payload is only the dataset id).
- **Not covered:** Phoenix traces and container logs (they expire with Phoenix's retention) and
  golden rerun reports written by the operator's `goldens run` (one file mixes datasets, and
  nothing in the database points to it). Both are in the threat model's residual risks and the
  README.
- **Staging leak fixed on the way.** `prepare_dataset` and `rebuild_dataset` never removed the
  ingest output in `staging_root`, and nothing links it to a dataset. The ingest and build
  handlers now remove their staging folder when they finish, and the worker removes staging
  folders older than 24 h at start and in every delete job. This also clears folders left by
  earlier ingests.

**Alternatives:**
- **Delete in the API (approach B):** answer 409 while any job of the dataset runs, and remove
  files after the transaction. Rejected: the user would have to wait for a run or an ingest to end
  and ask again, a crash between the commit and the file removal leaves files that nothing points
  to, and a slow file delete blocks a request.
- **Soft delete (hide only):** keeps every copy of the data on disk and in Postgres, which fails
  the purpose of removing one's own data.

**Consequences:**
- Ops numbers computed from `jobs` (job outcomes, latency SLOs over a window) lose the deleted
  jobs, so a past window can change after a deletion. Prometheus counters are not affected.
- A run being deleted may spend a little more while it finishes its current model call.
- Phoenix traces and golden rerun reports outlive the dataset; the operator owns them.
- `job_attempts_total` gains a `result` value, `wait`; no alert rule uses attempt results.
- A failed delete leaves the dataset hidden until the user asks again; there is no operator
  command for it.
- The ingest and build staging leak is fixed, and old leftovers are purged.
- ADR 0004's "deleting a dataset means deleting a file" no longer holds; it points here.
