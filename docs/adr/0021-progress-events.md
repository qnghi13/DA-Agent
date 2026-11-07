# 0021: Run progress as events in Postgres, streamed with SSE

**Status:** Accepted, 2026-11-08

**Context:** A run takes from seconds to minutes in the worker, while the client talks to the API,
another process. The UI should show the plan, each query, and verification as they happen, and a
dropped connection should not lose them.

**Decision:**
- `run_graph(on_event=...)` streams the graph (`stream_mode=["updates", "values"]`) instead of
  invoking it, and turns each node's update into a small event: `plan`, `turn` (the tools a model
  turn called), `tool` (name, success, SQL, row count, error, whether it drew a chart), `verify`
  (passed, problems), `done` (stop reason), and `question` when an interactive run pauses. Events
  never carry query rows or message text. A failing sink is recorded in the trace and ignored.
- The run job appends events to a `run_events` table. `GET /v1/runs/{id}/events` sends them as
  server-sent events with their table id as the SSE id, polls every 0.5 s for more, sends a
  keep-alive comment every 15 s, and ends with an `end` event holding the job status once the job
  has succeeded or failed. A client that reconnects with `Last-Event-ID` gets only what it missed.
  One connection lasts at most 5 minutes; the client then reconnects.
- `verify_problems` joins the graph state so the `verify` event can name what failed.

**Consequences:**
- Events survive API restarts and are readable by any API instance, at the cost of a query every
  half second per open stream.
- A run resumed after a killed worker repeats the events of the step that was running; the client
  may see one `turn` or `tool` twice.
- Before sending `end`, the stream reads once more, so events written just before the job finished
  are not lost to the race between reading events and reading the status.

**Rejected:**
- `LISTEN/NOTIFY` alone: notifications are lost while no one listens, so reconnecting needs the
  table anyway. It can be added later to wake streams instead of polling.
- WebSockets: the stream only goes one way, and SSE reconnects with `Last-Event-ID` for free.
- Streaming from the worker straight to the client: the client is connected to the API, not to
  the worker that happens to hold the job.
