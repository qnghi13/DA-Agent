# 0018: The worker: heartbeat thread, error classes, and the job id as the graph thread

**Status:** Accepted, 2026-11-08

**Context:** ADR 0017 gives a queue with leases. A worker must keep a lease alive while the graph
runs for minutes, decide which failures to retry, and make a job that is claimed again continue
rather than restart.

**Decision:**
- `Worker.run_once` claims one job, starts a heartbeat thread with its own connection (a psycopg
  connection must not be used by two threads at once), runs the handler for the job's kind, and
  writes the outcome only while it still owns the job. The handler gets an event that is set if
  the lease was lost.
- Three outcomes: a returned dict completes the job; `RetryableJobError` queues it again after the
  next delay in `retry_delays`; any other `Exception` fails it at once and stores only the
  exception type, since the job row is shown to the workspace and a message can hold host paths.
  The trace keeps the full exception. A `BaseException` such as a stopped process is not caught:
  the lease runs out and another worker takes the job.
- `RunHandler` runs the graph with the job id as run id and thread id, on a Postgres checkpointer.
  A provider failure leaves the run at its last checkpoint and becomes `RetryableJobError`, so the
  retry continues the run. The result is stored as JSON in `jobs.result`, with the query rows the
  report needs.
- The handler finds the dataset through a resolver keyed by workspace and dataset id, so a job can
  only open datasets of its own workspace. The datasets table comes with ingestion jobs.

**Consequences:**
- Tests on a real Postgres kill a worker mid-run and show another one finishing the job without
  repeating a finished model turn, and show a provider failure resumed by the retry. Turning off
  the heartbeat, or giving the graph a new thread id, makes them fail.
- A permanent provider error, such as a revoked key, is retried until attempts run out. Telling it
  apart needs the error class from `LLMClient`, which can be added when it matters.
- Results live in Postgres until an artifact store exists. A run keeps at most its bounded query
  rows and three charts.

**Rejected:**
- A process per job: isolation the sandbox already gives, at the cost of startup time.
- asyncio workers: the graph, the LLM client, and the Runner are synchronous; threads for the
  heartbeat are simpler.
- Retrying every exception: a bug would run the same failing job until its attempts run out, and
  each attempt can cost model calls.
