# 0042: Runs stop cooperatively on a cancel, a deadline, or a lost lease; operators time travel

**Status:** Accepted, 2026-11-20. Design:
[cancel, deadline, time travel](../design/2026-11-20-cancel-deadline-time-travel.md).

**Context:** A user could not stop a run that was going nowhere. Runs were limited by iterations,
tokens, and money, not wall time. A worker whose lease ran out kept calling the model for a job
another worker had taken over. Postgres kept a checkpoint after every node, but no tool read them.

**Decision:**
- **Stop token** (`da_agent.agent.stop`): one per run, with a deadline (`AgentLimits.max_seconds`,
  300 s by default) and a probe. The graph and the loop agent check it before every model call and
  between nodes; `LLMClient` asks it during waits for a limit or a retry, in one-second slices,
  and ends the wait with `CallInterruptedError` (no retry, no fallback, no ledger row). A stopped
  run ends with stop reason `cancelled`, `deadline_exceeded`, or `lease_lost`, and keeps its
  checkpoints. A model call or sandbox query already in flight finishes within its own timeout.
- **Cancel:** `POST /v1/runs/{id}/cancel`. A queued job ends `cancelled` at once and is never
  claimed. A running job gets `cancel_requested_at`; its run's probe reads it about once a
  second, and the worker ends the job `cancelled` with what it found so far. A job that fails or
  whose lease expires after a cancel request ends `cancelled`, not retried. A finished run gives
  409; asking twice changes nothing; every request is in the audit log.
- **Deadline:** a run past it ends `deadline_exceeded`, and the job succeeds, as with
  `budget_exceeded`: an outcome of the run, not a failure to retry. A deadline that passes during
  model retries is the deadline, not `llm_error`.
- **Lost lease:** the probe sees the heartbeat's `lost` event and stops the run; the worker does
  not complete or cancel a job it lost.
- **UI:** the send button becomes Stop while the latest question is queued or running, as in other
  chat assistants (user, 2026-11-20); Enter does not send meanwhile. A stopped run shows "Stopped".
- **Time travel CLI** (`python -m da_agent.timetravel list | show | fork`), for operators. `fork`
  copies one checkpoint, without its pending writes, into a new thread `fork-<run>-<suffix>` and runs on
  with the current code, through `RunHandler.fork`, so the dataset, documents, routes, and limits
  are the worker's. Costs go to the ledger under the fork's id, `fork-<run>-<suffix>`, which the
  ledger charges to the run's workspace (ADR 0043).

**Rejected:**
- Killing the run's thread: Python cannot. A process per run: too heavy here.
- A watcher thread per run, or the 15-second heartbeat, to learn of a cancel: the probe on the
  run's own thread is simpler to test and stops within about a second.
- Postgres LISTEN/NOTIFY: one more connection and failure mode for a second's gain.
- `failed` or `succeeded` for a cancelled job: one hides a user choice among errors, the other is
  wrong on the operator dashboard.
- Forking on the run's own thread from a `checkpoint_id`: the fork would become the thread's
  latest state. Copying pending writes into the fork: they are the next step's results, so the
  step would be replayed instead of run again.
- Time travel in the UI: frontend work, and forks spend a workspace's budget.

**Consequences:**
- A stop waits for the model call or query in flight, so it takes up to that call's timeout. It
  never starts another call: the client checks before each route and attempt, and during waits.
  Tried in the UI while a run waited on its rate limit: the job ended `cancelled` in 1.4 s.
- The deadline counts per attempt: a job retried after a provider failure, or resumed after a
  lost lease, gets a fresh `max_seconds`.
- Eval runs get the same 300 s default. Questions that used to queue longer behind a rate limit
  now end `deadline_exceeded`, and a recorded run that hit the deadline does not replay the same
  way (ADR 0039); compare with older baselines knowing this. `max_seconds` is in the manifest.
- A stopped run keeps the queries it ran, to read; exporting or rerunning them needs a finished
  run, so the UI hides those actions.
- A paused run (`needs_clarification`) ends its job `succeeded`, so it cannot be cancelled. If
  resuming with a reply is wired later, cancelling a paused run needs its own rule.
- A cancelled run cannot be resumed; the question can be asked again.
- A fork reads the job's current dataset and memory, not a snapshot from when it ran.
