# 0011: Agent SQL runs in a new sandbox per query, called as a library

**Status:** Accepted, 2026-11-06

**Context:** The agent's `run_sql` ran in the eval process on a trusted SQLite file. Clean datasets
built from uploads are untrusted, so queries must run in the sandbox (ADR 0005). Two choices were
open: one container per query or one per run, and how the Worker reaches the Runner. A sandbox
container starts in about 0.3 s on the dev machine, and a question runs 3–6 queries.

**Decision:**
- Each query starts a new container with the dataset mounted read-only, opens DuckDB read-only
  with external access off, returns a bounded result, and exits.
- The Worker calls the Runner as a library with typed requests. When the Worker and Runner become
  separate services (Days 16–17), an internal HTTP API carries the same request types.

**Consequences:**
- No state survives between queries, so one query cannot prepare an attack for the next.
- About 1–2 s of extra latency per question, small next to 6–9 LLM calls.
- No new service or queue before there is a Worker service to call it.
- Session features such as temporary tables are not available to the agent.

**Rejected:**
- One container per run: faster, but needs lifecycle, cleanup, and per-run state handling.
- Postgres job table between Worker and Runner: durable, but premature without a Worker service.
- Internal HTTP now: closer to the final shape, but adds a service and internal auth before use.
