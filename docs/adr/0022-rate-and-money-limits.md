# 0022: Rate limits and daily money limits in Postgres

**Status:** Accepted, 2026-11-08

**Context:** The threat model asks for rate limits against stolen or guessed keys, and for token
and money limits per run, day, and workspace. The cost ledger lived in each process's memory, so a
run retried by another worker started with its whole budget again, and nothing summed a
workspace's spending.

**Decision:**
- `PostgresCostLedger` subclasses `CostLedger` and writes every model call to `llm_calls`, with the
  workspace of the job whose id is the run id. `LLMClient`'s budget check reads it, so a run's
  spending survives retries and worker changes.
- A workspace may spend a daily budget (operator default, or `workspaces.daily_budget_usd`), counted
  from midnight UTC. A job gets `min(per-run limit, its earlier spending + what the day has left)`,
  and fails with a clear message when nothing is left. The API refuses new uploads and runs with
  429 until midnight UTC once the day's budget is spent. Reads keep working.
- Rate limits are fixed windows in `rate_counters`, one atomic upsert per hit, shared by every API
  process: requests per key (300 a minute), new jobs per workspace (30 a minute), and failed keys
  per client address (20 a minute). Past the limit, the API answers 429 with `Retry-After`. Once an
  address has used up its failures, even a valid key from it waits for the window to end, so
  guessing gets no feedback.

**Consequences:**
- A budget check runs before each model call, so one call can pass the limit; a call costs
  cents at most with the models in use.
- Fixed windows allow up to twice the limit across a window edge. That is enough to stop abuse;
  a sliding window can replace it without changing callers.
- Behind a reverse proxy, the client address is the proxy's unless the proxy's forwarded header is
  trusted. Compose will configure that; until then the per-address limit is per proxy.
- Every limit costs a query per request. Fine at this scale.

**Rejected:**
- Limits in process memory: each API process would count alone.
- Redis for counters: one more service for a few writes per request (ADR 0003 kept the stack to
  Postgres).
- A token bucket: smoother, but needs a read-modify-write per hit, where a fixed window is a single
  upsert.
