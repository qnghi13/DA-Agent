# Cancel, run deadline, and time travel: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build three things:
- runs that stop cooperatively on a user cancel, a deadline, or a lost lease;
- a Cancel button in the UI;
- an operator CLI that lists, shows, and forks a run's checkpoints.

**Architecture:**
- A `StopToken` is checked before model calls and between nodes, and raises `RunStopped`.
- Jobs gain a `cancelled` status and a cancel request that the heartbeat reads.
- The time travel CLI reads the Postgres checkpointer. It forks into a new thread.

**Tech Stack:** Python 3.12, LangGraph with PostgresSaver, FastAPI, psycopg 3, React with
TypeScript and vitest.

**Spec:** [2026-11-20-cancel-deadline-time-travel.md](2026-11-20-cancel-deadline-time-travel.md)

## Global Constraints

- **Tests first.** Unit tests use fake clocks and a fake LLM. They never sleep for real and never
  call a model.
- **Defaults:**
  - `max_seconds` is 300.
  - The heartbeat stays at 15 s.
  - The stop watcher polls every 1 s.
- **Stop reasons** are exactly `cancelled` and `deadline_exceeded`. The job status is exactly
  `cancelled`.
- **A fork never writes to the original run's thread or job.**
- **No new dependencies.**

## Review Focus

1. A cancel that arrives between claim and the first heartbeat, or after the run finished but
   before `complete`. The job ends in exactly one terminal status.
2. A run interrupted for clarify (paused) is cancelled. The paused job is not running, so the
   API must still end it.
3. A deadline that expires inside a model call's retries ends the run. It does not end in
   `llm_error`, which would make the job retry.
4. A fork of a checkpoint from before the run's first node, and a fork of the final checkpoint.
   Both give a clear result or a clear error, never a crash.
5. Two clicks on Cancel, or a cancel of another workspace's run. Both are idempotent, and a
   cancel of another workspace's run returns 404.

---

### Task 1: Stop token in the agents

**Files:**
- Create `src/da_agent/agent/stop.py`.
- Modify:
  - `src/da_agent/agent/loop_types.py` (`AgentLimits.max_seconds: float | None = 300.0`)
  - `src/da_agent/agent/graph.py`
  - `src/da_agent/agent/loop.py`
- Test:
  - `tests/unit/test_agent_stop.py`
  - `tests/unit/test_agent_graph.py`
  - `tests/unit/test_agent_loop.py`

**Interfaces:**
- **Produces:**
  - `StopToken(deadline_seconds: float | None = None, clock=time.monotonic)` with:
    - `.cancel(reason: str) -> None`
    - `.check() -> None`
    - `.reason: str | None`
  - `RunStopped(Exception)` with `.reason`.
  - `run_graph(..., stop: StopToken | None = None)` and `run_agent(..., stop=None)`.

- [ ] **Step 1: Write the tests.**
  - The token raises after its deadline, and not before.
  - The first `cancel` reason wins.
  - A graph run whose token is cancelled before its second model call ends with
    `stop_reason == "cancelled"`. The fake LLM is called once.
  - A deadline that passes during a node ends with `deadline_exceeded`. The fake clock advances
    inside the fake LLM.
  - With a checkpointer, the stopped run's last checkpoint stays readable.
  - The loop agent stops the same way.
  - A deadline inside `LLMClient` retries ends as `deadline_exceeded`, not `llm_error`.
    Implementation note: `_complete` checks the token before each call; retries inside the
    client are bounded by their own waits.
- [ ] **Step 2: Run the tests.** They fail.
- [ ] **Step 3: Implement.** The checks go in `_complete` (graph), before each model call in
  `loop.py`, and between stream chunks in `_stream`.
- [ ] **Step 4: Run the tests.** They pass, and the whole unit suite passes.

### Task 2: Deadline settings

**Files:**
- Modify:
  - `src/da_agent/config.py` (`DA_AGENT_RUN_MAX_SECONDS`, default 300)
  - `src/da_agent/worker/__main__.py` and `worker/runs.py` (limits with `max_seconds`)
  - `eval/datamate/run.py` (`--max-seconds`, default 300)
- Test:
  - `tests/unit/test_config.py`
  - `tests/unit/test_datamate_eval.py`

- [ ] **Tests:**
  - The setting is parsed and rejects non-positive or non-finite values.
  - The eval passes `max_seconds` into `AgentLimits`, and the manifest shows it.
- [ ] Implement, then run the tests.

### Task 3: Cancelled jobs in the queue and the worker

**Files:**
- Create `src/da_agent/storage/migrations/0015_cancel.sql`.
- Modify:
  - `src/da_agent/storage/jobs.py`
  - `src/da_agent/worker/worker.py`
  - `src/da_agent/worker/runs.py`
- Test:
  - `tests/integration/test_jobs.py` (or the existing queue test file)
  - `tests/integration/test_worker.py`

**Interfaces:**
- **Produces:**
  - `JobQueue.request_cancel(job_id, workspace_id) -> Literal["cancelled", "cancelling", "finished"] | None`.
    `None` means the job was not found in that workspace.
  - `JobQueue.cancel_requested(job_id) -> bool`.
  - `JobQueue.finish_cancelled(job_id, worker_id, result) -> bool`.
  - `RunHandler.__call__(job, lost, cancelled)`. Other handlers accept and ignore `cancelled`.

- [ ] **Step 1: Write the tests.**
  - A queued job is cancelled at once and is never claimed.
  - A running job is marked `cancelling`. Its heartbeat sets `cancelled`, and the run ends with
    job status `cancelled`, result stop reason `cancelled`, and `finished_at` set.
  - A lost lease stops the run, and the job is not completed by this worker.
  - A finished job returns `finished`.
  - An expired job with a cancel requested ends `cancelled`.
  - Cancelling twice is idempotent.
- [ ] **Step 2: Run the tests.** They fail.
- [ ] **Step 3: Implement.** The heartbeat checks `cancel_requested`. `RunHandler` runs a
  watcher thread that turns `lost` and `cancelled` into `token.cancel(...)`. The worker maps a
  result with stop reason `cancelled` to `finish_cancelled`.
- [ ] **Step 4: Run the tests.** They pass.

### Task 4: Cancel API and ops

**Files:**
- Modify `src/da_agent/api/app.py` and the ops summary module.
- Test `tests/integration/test_api_runs.py` (or the existing run API tests), and the ops tests.

- [ ] **Tests:**
  - `POST /v1/runs/{id}/cancel` returns 202 with `{"status": "cancelled"}` for a queued run, and
    `{"status": "cancelling"}` for a running one.
  - It returns 409 for a finished run and 404 for another workspace's run.
  - A paused (clarify) run ends cancelled.
  - The audit log gets `run.cancel`.
  - `GET /v1/runs/{id}` shows `cancel_requested`.
  - The ops summary counts cancelled runs.
- [ ] Implement, then run the tests.

### Task 5: Cancel button

**Files:**
- Modify `web/src/api.ts`, `web/src/run.ts`, `web/src/Conversation.tsx`, and `status.ts` if it
  maps statuses.
- Test `web/src/run.test.ts`, or a new `cancel.test.ts`.

- [ ] **Tests (vitest):**
  - `cancelRun(id)` posts to the right path.
  - The status helper labels `cancelled` "Cancelled".
  - A trail with status `cancelled` stops polling.
- [ ] Implement the button. It shows only while queued or running, reads "Cancelling…" after a
  click, and disables itself.
- [ ] Run `npm test` and the typecheck in `web/`. Then run the app and click Cancel on a slow
  run, using the run skill.

### Task 6: Time travel CLI

**Files:**
- Create `src/da_agent/timetravel.py`.
- Test `tests/integration/test_timetravel.py`, with a fake LLM and the Postgres checkpointer.

**Interfaces:**
- **Produces:**
  - `list_checkpoints(saver, run_id) -> list[CheckpointRow]`
  - `show_checkpoint(saver, run_id, checkpoint_id) -> dict`
  - `fork(run_id, checkpoint_id, *, saver, build_app_inputs...) -> AgentResult`
  - `main(argv) -> int`

- [ ] **Step 1: Write the tests.**
  - `list` gives steps in order, with node names and stop reasons.
  - `show` cuts long values.
  - `fork` from the checkpoint after `plan` reruns from `agent`, on a new thread with a new run
    id. The original thread's latest checkpoint and the job are unchanged.
  - An unknown checkpoint gives a clear error and exit code 1.
  - A fork of the final checkpoint returns the same result without calling the model.
- [ ] **Step 2: Run the tests.** They fail.
- [ ] **Step 3: Implement.** The CLI builds the dataset, documents, and `LLMClient` the way the
  worker does, from `Settings`, for the job's workspace and dataset.
- [ ] **Step 4: Run the tests.** They pass.

### Task 7: Docs

- [ ] ADR 0042.
- [ ] Learning note 21, in Vietnamese.
- [ ] `architecture.md`: job statuses and the stop token.
- [ ] `docs/runbook` or the README: how to use the time travel CLI.
- [ ] `.env.example`: `DA_AGENT_RUN_MAX_SECONDS`.
- [ ] `progress.md`.

### Final

- Run `uv run --env-file .env pytest`, `uv run ruff check .`, and `npm test` in `web/`.
- Then a fresh whole-branch review on the most capable model.
- No real LLM call is needed.
