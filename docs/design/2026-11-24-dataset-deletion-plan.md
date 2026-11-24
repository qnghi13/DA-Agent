# Dataset Deletion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `DELETE /v1/datasets/{id}` hides a dataset at once and a background `delete` job removes
every file, checkpoint, and row that holds its data.

**Architecture:** The API marks the dataset `deleting`, cancels its jobs, and queues a `delete`
job (one transaction). Storage hides `deleting` datasets and their jobs from every read, and job
writers cannot bring them back. The worker's `DeleteHandler` waits (without spending attempts)
until no job of the dataset runs, then removes files, then checkpoint threads, then rows.

**Tech Stack:** Python 3.12, FastAPI, psycopg 3, LangGraph `PostgresSaver`, pytest; React +
TypeScript + Vitest.

**Spec:** [2026-11-24-dataset-deletion.md](2026-11-24-dataset-deletion.md) (approved 2026-11-24).
Read its inventory tables before Task 5.

## Global Constraints

- Tests first (`tdd` skill). Postgres tests run with `uv run --env-file .env pytest`; they need
  `DA_AGENT_TEST_POSTGRES_URL` and are marked `integration`.
- Commit locally per task on branch `feat/dataset-deletion` (the user agreed 2026-11-24); never push.
- No new dependency.
- The delete never calls `_may_start_job`: no budget, rate, or slot check.
- A `deleting` dataset is 404 for every member route; its jobs are 404 too.
- Kept on purpose: `llm_calls`, `audit_log`, and the `delete` job's own row.
- The wait limit is 2 h (7200 s), measured as the delete job's age (`Job.queue_wait_seconds` at
  claim). The staging age limit is 24 h (86400 s).
- Code, docs, and ADRs in English; UI strings in `en.ts` and `vi.ts`; learning note in Vietnamese.
- `uv run ruff check .`, `uv run ruff format .`, `uv run pytest` (and `npm test` in `web/`) pass at
  the end of every task.

## Review Focus

1. A run that is mid-answer when its dataset is deleted: it must end `cancelled`, write nothing
   after the files go, and its checkpoint thread must be gone afterwards. Test in Task 5
   (`test_a_running_run_blocks_the_delete_until_it_ends`).
2. An ingest that finishes while the dataset is `deleting`: the dataset must stay hidden and its
   build must still be removed. Tests in Task 2 (`test_finishing_jobs_cannot_bring_back_a_deleting_dataset`)
   and Task 5 (`test_a_build_installed_during_the_wait_is_removed`).
3. Two `DELETE` requests at once, or one after a failed delete: one open delete job at most, and
   a new one after a failure. Test in Task 3 (`test_deleting_twice_queues_one_job_and_a_failed_delete_can_be_retried`).
4. A crash between steps: a retry must finish the work with no error on files or rows already
   gone. Test in Task 5 (`test_a_delete_finishes_after_a_crash_at_any_step`).
5. Another workspace's dataset id: 404, nothing changes. Test in Task 3
   (`test_another_workspace_cannot_delete_a_dataset`).

---

### Task 1: Schema, queue methods, and a wait that spends no attempt

**Files:**
- Create: `src/da_agent/storage/migrations/0018_dataset_deletion.sql`
- Modify: `src/da_agent/storage/jobs.py` (`JobKind`, new methods)
- Modify: `src/da_agent/worker/worker.py` (new `JobWaitError`, handled in `_process`)
- Test: `tests/integration/test_job_queue.py`, `tests/integration/test_worker.py`

**Interfaces:**
- Produces:
  - Migration: `datasets.status` CHECK gains `'deleting'`; `jobs.kind` CHECK gains `'delete'`.
  - `JobKind` gains `"delete"`.
  - `JobQueue.cancel_dataset_jobs(workspace_id: UUID, dataset_id: UUID, *, except_id: UUID | None = None) -> None`:
    `request_cancel` semantics for every `queued` or `running` job with
    `payload ->> 'dataset_id' = dataset_id`, except `except_id` and except kind `delete`.
  - `JobQueue.running_jobs_of_dataset(dataset_id: UUID, *, except_id: UUID) -> int`.
  - `JobQueue.wait(job_id: UUID, worker_id: str, *, seconds: float) -> bool`: a running job of
    this worker goes back to `queued` with `run_after = now() + seconds`, `attempts - 1`,
    `lease_until NULL`; False if the worker no longer holds it.
  - `class JobWaitError(Exception)` in `worker.py` with `seconds: float`; the worker calls
    `queue.wait`, records the attempt as `"wait"`, and leaves `last_error` unchanged.

- [ ] **Step 1: Write the failing tests**

```python
# test_job_queue.py
def test_dataset_jobs_are_cancelled_except_the_named_one_and_deletes(conn, workspace):
    # queued run -> cancelled; running query -> cancel_requested_at set, still running;
    # job of another dataset untouched; except_id untouched; a 'delete' job of d untouched.

def test_a_wait_puts_the_job_back_without_spending_an_attempt(conn, workspace):
    job = claim(...)                      # attempts == 1
    assert JobQueue(conn).wait(job.id, "w1", seconds=30)
    row = status_attempts_run_after(job.id)
    assert row.status == "queued" and row.attempts == 0 and row.run_after > now()
    assert not JobQueue(conn).wait(job.id, "w1", seconds=30)   # no longer held

# test_worker.py
def test_a_waiting_handler_is_queued_again_with_its_attempt_given_back(...):
    # handler raises JobWaitError(seconds=5); job ends queued, attempts 0, last_error None
```

- [ ] **Step 2: Run them and see them fail**

Run: `uv run --env-file .env pytest tests/integration/test_job_queue.py tests/integration/test_worker.py -k "cancelled_except or wait" -v`
Expected: FAIL (`AttributeError: cancel_dataset_jobs`, `ImportError: JobWaitError`).

- [ ] **Step 3: Write the migration, the three queue methods, and `JobWaitError` handling**

The migration follows `0015_cancel.sql` (drop and re-add each CHECK constraint, header comment
pointing to the design doc). `cancel_dataset_jobs` reuses the `CASE` logic of `request_cancel`.

- [ ] **Step 4: Run the tests and see them pass**

Run: `uv run --env-file .env pytest tests/integration/test_job_queue.py tests/integration/test_worker.py -v`
Expected: PASS.

### Task 2: The `deleting` state in storage: start, hide, guard

**Files:**
- Modify: `src/da_agent/storage/datasets.py`, `src/da_agent/storage/jobs.py` (`get`),
  `src/da_agent/storage/memory.py` (`_require_dataset`, `_get`, `ConversationStore.get`),
  `src/da_agent/storage/dataset_versions.py` (`mark`, `apply`)
- Test: `tests/integration/test_datasets.py`, `tests/integration/test_memory.py`,
  `tests/integration/test_dataset_versions_store.py`

**Interfaces:**
- Consumes: `JobQueue.cancel_dataset_jobs` (Task 1).
- Produces:
  - `DatasetStore.start_delete(workspace_id: UUID, dataset_id: UUID) -> UUID | None`: in one
    transaction, `FOR UPDATE` on the row (any status); None if absent. Sets `deleting`, calls
    `cancel_dataset_jobs`, and enqueues a `delete` job with payload `{"dataset_id": str(d)}`
    unless a `queued` or `running` delete job of `d` exists. Returns the open delete job's id.
  - `lock_live_dataset(conn: psycopg.Connection, workspace_id: UUID, dataset_id: UUID) -> bool`
    in `storage/datasets.py`: `SELECT ... FOR SHARE` where status is not `deleting`; must run
    inside a transaction.
  - Hiding: `DatasetStore.get` and `in_workspace` skip `deleting` rows; `JobQueue.get` returns
    None for a job whose `payload ->> 'dataset_id'` names a `deleting` dataset; memory and
    conversation lookups by dataset or by entry id raise `MemoryNotFoundError` for a `deleting`
    dataset.
  - Guards: `mark_proposed`, `mark_ready`, `mark_failed`, `DatasetVersionStore.mark` and `apply`
    change nothing when the dataset is `deleting` (`mark` returns False).

- [ ] **Step 1: Write the failing tests**

```python
def test_start_delete_hides_the_dataset_and_queues_one_delete_job(conn, workspace):
    d = ready_dataset(...); run = enqueue run of d
    job_id = DatasetStore(conn).start_delete(workspace, d)
    assert DatasetStore(conn).get(d, workspace) is None
    assert all(x["id"] != str(d) for x in DatasetStore(conn).in_workspace(workspace))
    assert JobQueue(conn).get(run, workspace) is None
    assert kind_and_payload(job_id) == ("delete", {"dataset_id": str(d)})
    assert DatasetStore(conn).start_delete(workspace, d) == job_id      # no second job
    assert DatasetStore(conn).start_delete(other_workspace, d) is None

def test_finishing_jobs_cannot_bring_back_a_deleting_dataset(conn, workspace):
    # start_delete, then mark_proposed / mark_ready / mark_failed / version mark and apply:
    # status stays 'deleting'; DatasetVersionStore.mark returns False.

def test_memory_and_conversations_of_a_deleting_dataset_are_not_found(conn, workspace):
    # MemoryStore.write, approve, delete and ConversationStore.create, get raise MemoryNotFoundError

def test_lock_live_dataset_refuses_a_deleting_dataset(conn, workspace): ...
```

- [ ] **Step 2: Run them and see them fail**

Run: `uv run --env-file .env pytest tests/integration/test_datasets.py tests/integration/test_memory.py tests/integration/test_dataset_versions_store.py -k "delet" -v`
Expected: FAIL (`AttributeError: start_delete`).

- [ ] **Step 3: Implement `start_delete`, `lock_live_dataset`, the hiding filters, and the guards**

A guard is `AND status <> 'deleting'` on the dataset update, or `AND NOT EXISTS (SELECT 1 FROM
datasets d WHERE d.id = ... AND d.status = 'deleting')` on version and job queries.

- [ ] **Step 4: Run the whole integration suite**

Run: `uv run --env-file .env pytest tests/integration -v`
Expected: PASS (existing worker tests still find their datasets).

### Task 3: API: `DELETE /v1/datasets/{id}` and closing the enqueue race

**Files:**
- Modify: `src/da_agent/api/app.py`
- Test: `tests/integration/test_api_delete.py` (new; reuse fixtures from `test_api.py`)

**Interfaces:**
- Consumes: `DatasetStore.start_delete`, `lock_live_dataset` (Task 2).
- Produces: `DELETE /v1/datasets/{dataset_id}` → `202 {"id": d, "status": "deleting"}`, audit
  action `dataset.delete`. `create_run`, `create_query`, both `create_export` routes, and
  `approve_version` call `lock_live_dataset` in the transaction that enqueues and answer 404 when
  it returns False.

- [ ] **Step 1: Write the failing tests**

```python
def test_delete_hides_the_dataset_at_once_and_is_logged(client, conn, key):
    d = ready_dataset(client, conn, key)
    r = client.delete(f"/v1/datasets/{d}", headers=auth(key))
    assert r.status_code == 202 and r.json() == {"id": d, "status": "deleting"}
    assert d not in [x["id"] for x in client.get("/v1/datasets", headers=auth(key)).json()["datasets"]]
    assert "dataset.delete" in audit_actions(conn)

@pytest.mark.parametrize("method,path", [  # every route of a deleting dataset or its jobs
    ("GET", "/v1/datasets/{d}"), ("GET", "/v1/datasets/{d}/versions"),
    ("GET", "/v1/datasets/{d}/memory"), ("GET", "/v1/runs?dataset_id={d}"),
    ("GET", "/v1/runs/{run}"), ("GET", "/v1/runs/{run}/events"), ("GET", "/v1/runs/{run}/report"),
    ("POST", "/v1/runs"), ("POST", "/v1/datasets/{d}/files"), ("POST", "/v1/datasets/{d}/approve"),
    ("POST", "/v1/runs/{run}/queries/0/export"), ("POST", "/v1/runs/{run}/queries"),
    ("GET", "/v1/queries/{query}"), ("GET", "/v1/exports/{export}"),
    ("GET", "/v1/exports/{export}/file"), ("PUT", "/v1/runs/{run}/feedback"),
    ("POST", "/v1/conversations"), ("POST", "/v1/datasets/{d}/memory"),
    ("POST", "/v1/datasets/{d}/metrics"), ("DELETE", "/v1/memory/{memory}"),
])
def test_every_route_of_a_deleting_dataset_is_not_found(...): assert status == 404

def test_deleting_twice_queues_one_job_and_a_failed_delete_can_be_retried(...):
    # two DELETEs -> one open delete job; mark it failed; DELETE again -> a new queued one

def test_another_workspace_cannot_delete_a_dataset(...): # 404, status unchanged, no job

def test_delete_works_with_no_budget_left_and_at_the_job_limit(...): # 202
```

- [ ] **Step 2: Run them and see them fail**

Run: `uv run --env-file .env pytest tests/integration/test_api_delete.py -v`
Expected: FAIL (405 Method Not Allowed on `DELETE`).

- [ ] **Step 3: Add the route and the `lock_live_dataset` checks**

- [ ] **Step 4: Run the API tests**

Run: `uv run --env-file .env pytest tests/integration -k "api" -v`
Expected: PASS.

### Task 4: Staging is cleaned by ingest and build, and old staging is purged

**Files:**
- Create: `src/da_agent/worker/staging.py`
- Modify: `src/da_agent/worker/datasets.py` (`IngestHandler`, `BuildHandler`),
  `src/da_agent/worker/__main__.py` (purge once at start)
- Test: `tests/unit/test_staging.py` (new), `tests/integration/test_datasets.py`

**Interfaces:**
- Produces: `purge_staging(root: Path, *, now: float, max_age_seconds: float = 86400) -> int` —
  removes direct child directories of `root` whose mtime is older than `max_age_seconds`;
  returns how many; a missing root returns 0. `build_worker` calls it on `settings.staging_root`.

- [ ] **Step 1: Write the failing tests**

```python
def test_only_staging_folders_older_than_a_day_are_purged(tmp_path):
    old, fresh = make dirs; os.utime(old, (now - 86401,) * 2)
    assert purge_staging(tmp_path, now=now) == 1
    assert not old.exists() and fresh.exists()

def test_ingest_and_build_leave_no_staging_folder(...):
    # WritingRunner writes its ingest output under tmp staging; after IngestHandler and
    # BuildHandler (success and failure) the staging root is empty
```

- [ ] **Step 2: Run them and see them fail**

Run: `uv run --env-file .env pytest tests/unit/test_staging.py tests/integration/test_datasets.py -k "staging" -v`
Expected: FAIL.

- [ ] **Step 3: Implement `purge_staging`, remove `prepared.ingest.output_dir` in a `finally`
  in both handlers, and call the purge from `build_worker`**

- [ ] **Step 4: Run the tests and see them pass**

Run: `uv run --env-file .env pytest tests/unit/test_staging.py tests/integration/test_datasets.py -v`
Expected: PASS.

### Task 5: The `delete` job

**Files:**
- Create: `src/da_agent/worker/deletion.py`
- Modify: `src/da_agent/agent/checkpoints.py` (move `RunHandler.checkpoint_connection` here as a
  function; `RunHandler` calls it), `src/da_agent/worker/__main__.py` (register `"delete"`)
- Test: `tests/integration/test_worker_delete.py` (new)

**Interfaces:**
- Consumes: Task 1 (`cancel_dataset_jobs`, `running_jobs_of_dataset`, `JobWaitError`), Task 4
  (`purge_staging`).
- Produces:
  - `checkpoint_connection(url: str, schema: str | None) -> psycopg.Connection[DictRow]`.
  - `delete_run_threads(saver: PostgresSaver, conn: psycopg.Connection, run_ids: list[str]) -> int`
    in `agent/checkpoints.py`: deletes thread `run_id` and every thread id `LIKE 'fork-<run_id>-%'`
    (read from the `checkpoints` table) with `saver.delete_thread`; returns threads deleted.
  - `DeleteHandler(*, url, schema=None, uploads_root, datasets_root, exports_root, staging_root,
    wait_seconds: float = 30.0, max_wait_seconds: float = 7200.0)`, a `Handler`. Steps, in order:
    no dataset row → `{"status": "deleted"}`; cancel again; running jobs and age >
    `max_wait_seconds` → `PermanentJobError("other jobs of the dataset did not stop")`; running
    jobs → `JobWaitError(wait_seconds)`; remove export CSVs (`export_file`) of export jobs of
    `d`; remove each version's `upload_dir` and `build_dir`, and every child of `uploads_root`
    and `datasets_root` named `d`, `d.old`, or starting with `d-v`; `purge_staging`; delete
    checkpoint threads of `d`'s run jobs; in one transaction delete `golden_cases` of `d`, jobs
    of `d` other than itself, then the `datasets` row. Returns `{"status": "deleted"}`. Every
    path is checked to be a direct child of its root.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.fixture
def full_dataset(...):
    # every inventory item of the spec: 2 versions (upload d and d-v2-ab12, builds d and d-v2,
    # plus d.old), a run with result, run_events, job_attempts, feedback, golden case,
    # conversation, note, metric, a query job, an export job with its CSV, a checkpoint thread
    # for the run and one fork thread 'fork-<run>-1a2b', an llm_calls row, an audit row;
    # plus a second dataset in the same workspace with its own folders and run

def test_a_delete_removes_the_whole_inventory_and_nothing_else(full_dataset): ...
    # every row and path of d gone; llm_calls, audit_log, the delete job, and the other
    # dataset's rows, folders, and checkpoint untouched

def test_a_running_run_blocks_the_delete_until_it_ends(...):
    # a running job of d -> JobWaitError, no file removed; after it ends cancelled -> deleted

def test_a_build_installed_during_the_wait_is_removed(...):
    # a build folder appears while waiting; the next attempt removes it

def test_a_delete_that_waited_too_long_fails(...):  # age > max_wait -> PermanentJobError

@pytest.mark.parametrize("crash_after", ["files", "checkpoints"])
def test_a_delete_finishes_after_a_crash_at_any_step(...):
    # monkeypatch the next step to raise; run again; whole inventory gone, no error

def test_a_delete_of_a_dataset_already_gone_succeeds(...)

@pytest.mark.security
def test_a_build_dir_outside_its_root_is_never_removed(...):
    # a version row with build_dir '../x' -> that path survives
```

- [ ] **Step 2: Run them and see them fail**

Run: `uv run --env-file .env pytest tests/integration/test_worker_delete.py -v`
Expected: FAIL (`ModuleNotFoundError: da_agent.worker.deletion`).

- [ ] **Step 3: Implement `checkpoint_connection`, `delete_run_threads`, and `DeleteHandler`;
  register it in `build_worker`**

- [ ] **Step 4: Run the worker tests and the whole suite**

Run: `uv run --env-file .env pytest tests/integration -v && uv run pytest`
Expected: PASS.

### Task 6: Web: the delete action

**Files:**
- Modify: `web/src/api.ts`, `web/src/DatasetView.tsx`, `web/src/Workspace.tsx`,
  `web/src/i18n/en.ts`, `web/src/i18n/vi.ts`
- Test: `web/src/datasets.test.ts` (new)

**Interfaces:**
- Consumes: `DELETE /v1/datasets/{id}` (Task 3).
- Produces: `Api.deleteDataset(id: string): Promise<void>`; `DatasetView` gets
  `onDeleted: () => void`; i18n keys `dataset.delete` ("Delete dataset" / "Xoá dataset") and
  `dataset.confirmDelete(name)` with the spec's text: "Delete {name}? Its files, every answer and
  chat about it, notes, and metrics are deleted for good."

- [ ] **Step 1: Write the failing test**

```ts
it("deletes a dataset by id", async () => {
  // fetch mock answers 202; api.deleteDataset("d/1")
  expect(url).toBe("/v1/datasets/d%2F1");
  expect(init?.method).toBe("DELETE");
});
it("words the confirmation in both languages", () => {
  expect(en.dataset.confirmDelete("Sales")).toContain("Sales");
  expect(viMessages.dataset.confirmDelete("Sales")).not.toBe(en.dataset.confirmDelete("Sales"));
});
```

- [ ] **Step 2: Run it and see it fail**

Run: `cd web && npx vitest run src/datasets.test.ts`
Expected: FAIL (`api.deleteDataset is not a function`).

- [ ] **Step 3: Add the API method, the button with `window.confirm` in `DatasetView`'s header,
  and `onDeleted` in `Workspace`**: remove the dataset from the list, select upload, and clear
  the browser's saved conversation (`saveConversation(datasetId, null)`).

- [ ] **Step 4: Run web tests and the type-checked build**

Run: `cd web && npm test && npm run build`
Expected: PASS.

- [ ] **Step 5: Check it in the real app** with the `run` skill: upload a small CSV, ask one
  question, delete, confirm the dataset leaves the list and its folders are gone on disk.

### Task 7: Records

**Files:**
- Create: `docs/adr/0050-dataset-deletion.md`, `docs/learning/27-xoa-dataset.md`
- Modify: `docs/adr/0004-duckdb-per-dataset.md` (pointer to 0050), `docs/adr/README.md`,
  `docs/threat-model.md` (control under threats and controls; Phoenix traces and golden reports
  under residual risks), `README.md` (deleting data, and what it does not cover),
  `docs/design/2026-11-24-dataset-deletion.md` (status: implemented), `docs/progress.md`

- [ ] **Step 1: Write the ADR** (context, decision, the alternatives from the spec's approach
  choice, consequences including the ops-window note) and the other doc changes.
- [ ] **Step 2: Run** `uv run ruff check . && uv run pytest` — Expected: PASS.
- [ ] **Step 3: Ask the user whether to commit.**
