# Cancel, run deadline, and time travel

**Status:** approved 2026-11-20. The user chose time travel as an operator CLI (option A) and a
Cancel button in the UI. **ADR:** [0042](../adr/0042-cancel-deadline-time-travel.md).

## Why

This is step 4 of the harness track, and it serves learning and operations.

**Cancel.** A user cannot stop a run that is going nowhere: it keeps spending until it hits its
token or money limit.

**Run deadline.** Runs are limited by iterations, tokens, and money, but not by wall time. With
60-second model timeouts and their retries, a run can take many minutes.

**Lease loss.** The worker sets a `lost` event when its lease on a job runs out, but `RunHandler`
ignores it. A run that another worker has taken over keeps calling the model on the old worker.

**Time travel.** Postgres keeps a checkpoint after every graph node, but nothing lets an operator
list a run's steps, read the state at one step, or rerun from it with today's code.

## Goals and non-goals

**Goals.**

- **Cooperative stop.** One stop signal per run has three sources: a user cancel, the deadline,
  and a lost lease. The graph, and the loop agent the eval uses, check it before every model call
  and between nodes. A stopped run ends with `stop_reason` `cancelled` or `deadline_exceeded`.
  Its checkpoints are kept.
- **Cancel.**
  - `POST /v1/runs/{id}/cancel` ends a queued run at once.
  - On a running run, the same call marks it, and the worker stops it at its next heartbeat
    (within about 15 seconds).
  - A finished run returns 409.
  - The job ends with a new status, `cancelled`. Every cancel is written to the audit log.
  - In the UI, the question's send button turns into a Stop button while a run is queued or
    running, as in ChatGPT and Claude (user, 2026-11-20: no separate button). The run then shows
    "Cancelled".
- **Deadline.** `AgentLimits.max_seconds`, 300 by default (`DA_AGENT_RUN_MAX_SECONDS` for the
  worker, `--max-seconds` for the eval). It is part of the manifest limits.
  - A run that passes it ends with `deadline_exceeded`. The job still succeeds, as with
    `budget_exceeded`, since that is an outcome of the run, not a failure of the job.
- **Lease loss.** The run stops at once, and the worker does not complete the job.
- **Time travel CLI.** `python -m da_agent.timetravel` is for operators, with the same
  environment as the worker:
  - `list RUN_ID`: each checkpoint with its step, node, next nodes, time, iterations, and stop
    reason.
  - `show RUN_ID CHECKPOINT_ID`: the state at that step as JSON, with long values cut.
  - `fork RUN_ID CHECKPOINT_ID`: copies that state into a new thread (`fork-<uuid>`) and runs the
    graph on from there with the current code, prompts, and routes. It prints the result. Costs
    go to the ledger under the fork's id. The original run's thread and job are never touched.

**Non-goals.**

- Killing a model call or sandbox query already in flight. They finish within their own
  timeouts.
- Resuming a cancelled run.
- Editing the state before a fork.
- Time travel in the UI or API.
- Forks that replay a cassette. Production runs do not record one.

**Anti-goals.**

- A cancel that leaves the job `running` forever.
- A fork that changes what the original run shows.
- A fork that bypasses the cost ledger.

## Approaches

| Question | Chosen | Rejected, and why |
|---|---|---|
| How a run stops | A stop token checked before each model call and between nodes (cooperative) | Killing the thread: Python cannot; a process per run: too heavy for this project |
| How a running worker learns of a cancel | The heartbeat, every 15 s, reads `cancel_requested_at` on its job | Postgres LISTEN/NOTIFY: faster, but one more connection and failure mode for a 15 s gain |
| Cancelled job status | A new terminal status, `cancelled` | `failed` with a message: hides a user choice among errors; `succeeded` with a stop reason: wrong in the ops dashboard |
| Deadline outcome | `succeeded` with stop reason `deadline_exceeded`, as budgets | A failed job: it would be retried, and spend again |
| Where time travel lives | An operator CLI (user chose A) | UI screen (B): frontend work, and forks spend the workspace's budget |
| How a fork starts | A new thread, seeded with `update_state(values, as_node=<the step's node>)` | Running from `checkpoint_id` on the same thread: the fork becomes the thread's latest state, which resumes and reports read |

## Design notes

**Stop token** (`da_agent.agent.stop`).

- `StopToken(deadline: float | None, clock)` has these methods:
  - `cancel(reason)`
  - `check()`, which raises `RunStopped(reason)`
  - `stopped`
- The first reason wins. The deadline counts from the moment the token is made.
- `run_graph` and `run_agent` take an optional `stop` token. Without one, they make a token from
  `limits.max_seconds`.
- Inside a node, `RunStopped` comes out of the stream. `run_graph` catches it and returns a
  result with that stop reason. State and queries are taken from the last checkpoint, or from
  memory when there is no checkpointer.

**Jobs.**

- Migration 0015 adds `cancelled` to `jobs.status` and adds a `cancel_requested_at` column.
- `JobQueue` gains three methods:
  - `request_cancel(job_id, workspace)`, which returns `cancelled`, `cancelling`, or `finished`.
  - `cancel_requested(job_id)`, which returns a bool.
  - `finish_cancelled(job_id, worker_id, result)`.
- `claim` never takes a job with a cancel requested.
- `fail_expired` turns an expired job with a cancel requested into `cancelled`.
- The heartbeat sets a `cancelled` event besides `lost`. `RunHandler` builds the token from both
  events: a small watcher thread checks them every second and cancels the token.

**API.**

- The run view shows `cancelled` and a `cancel_requested` flag.
- The ops summary counts cancelled runs.

**UI.**

- The `cancelled` status is added to the types in `api.ts` and `run.ts`.
- The send button becomes a Stop button (square icon, label "Stop") while the latest run is
  queued or running, with no confirmation (cancel is cheap and the question can be asked again).
  After a click it is disabled and reads "Stopping…" until the status turns `cancelled`.

## Left to the builder

- The exact UI wording and placement within the existing run card.
- How the CLI prints a table.
