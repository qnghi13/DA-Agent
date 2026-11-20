# 0041: A circuit breaker per process and outbound limits per route for model calls

**Status:** Accepted, 2026-11-20. Design: [LLM resilience](../design/2026-11-20-llm-resilience.md).

**Context:** `LLMClient` only reacted to trouble. When a provider failed, every request ran its
full retries (waits up to 30 seconds) before falling back, and nothing remembered the failure for
the next request. Nothing limited requests or tokens per minute before a call; a 429 was the only
brake, and eval worker threads met it together.

**Decision:**
- **Breaker** (`da_agent.llm.breaker`), one per route in each process. Five network failures in a
  row (`RateLimitedError`, `TransientLLMError`) open it for 30 seconds; then one caller probes.
  Any answer from the provider closes it, including permanent errors and invalid tool calls,
  which are the request's fault, not the provider's health. An open route is skipped with a
  `circuit_open` span event; retries stop when the breaker opens; every route open fails at once.
  A probe that learns nothing (our own limit refused it, or a bug raised) is given back, so the
  next caller probes; a probe that never reports back expires after one cooldown.
- **Outbound limits** (`da_agent.llm.ratelimit`), two token buckets per route: requests and tokens
  per minute. A call reserves its estimated tokens (characters / 4 plus its output cap) and
  `settle` replaces them with the real usage. A bucket may go negative, so callers queue in order;
  a wait above the route's limit reserves nothing and raises `ThrottledError`, which falls back to
  the next route, is never retried on the same route, never counts toward the breaker, and is
  recorded in the ledger as `throttled` (migration 0014 widens `llm_calls.error_kind`). Routes
  with a fallback wait at most `max_wait` (30 s); the last route waits up to `max_throttle_wait`
  (300 s), since queueing beats failing the run when nothing is left to fall back to.
- **Two limiter backends.** Memory, the default and the only one for eval runs. Postgres
  (`rate_buckets`, migration 0014) for worker replicas sharing a key: one transaction locks the
  requests row then the tokens row, refills from the database clock, and rolls back when the wait
  is too long. Its connection gives up within 2 seconds (connect, lock, and statement timeouts).
  Any limiter error lets the call through (fail open) and marks the span; after a failure the
  limiter skips the database for 10 seconds instead of reconnecting on every call.
- **Configuration:** `DA_AGENT_ROUTE_LIMITS` (JSON by route; unset means no limits),
  `DA_AGENT_RATE_LIMITER` (`memory` or `postgres`; Compose uses `postgres`),
  `DA_AGENT_BREAKER_FAILURES`, `DA_AGENT_BREAKER_COOLDOWN_SECONDS`. Strict replays get no limits.
- **Idempotent model calls on resume are not built.** The graph checkpoints after every node and
  each node calls the model at most once, so a resume repeats at most the call in flight, which has
  no side effect but its cost. `test_a_killed_run_resumes_without_repeating_finished_model_turns`
  proves it.

**Rejected:**
- A breaker shared through Postgres: each process sees failures within a few calls by itself, and
  sharing adds a dependency that can fail (Hystrix, Resilience4j, and Envoy keep breakers local).
- Only an in-memory limiter: N worker processes would each use the whole quota.
- Only a Postgres limiter: eval runs often have no database, and a round trip per call is not
  needed for one process.
- Failing closed when the limiter database is down: a limiter outage would stop every run, while
  the provider's 429 still protects the quota.

**Consequences:**
- The token estimate is rough; settling with the real usage keeps the bucket right over time.
- Limits are set by hand from the provider's published quota. Reading `x-ratelimit-*` response
  headers to follow the real remaining quota is a later add-on.
- Breaker state is lost on restart; the first calls after a restart probe the provider again.
- A bucket holds one minute's limit, so after an idle minute a burst of `rpm` calls goes out at
  once and up to twice `rpm` can fall in one 60-second window. A provider that enforces a sliding
  window may then answer 429, which retries and fallback handle. Set limits below the provider's
  quota (half of it for a strict sliding window).
- Any provider answer resets the breaker's failure count, so a provider that alternates 503s with
  request errors never opens it.
