# Circuit breaker and outbound rate limits for model calls

**Status:** approach approved 2026-11-20 (user: combined option). **ADR:** [0041](../adr/0041-llm-resilience.md).

## Why

Harness track, step 3 (learning, not score). Today `LLMClient` only reacts:

- When a provider is down, every request still runs its full retries, waiting up to 30 seconds
  on `retry-after`, before it falls back. Nothing remembers that the provider was failing a second
  ago.
- Requests and tokens per minute are never limited before a call. Only a 429 slows the client,
  and eval `--workers` threads hit it together.

## Goals and non-goals

- **Circuit breaker:** per route, per process. After repeated network failures the route is
  skipped for a cooldown, then one probe request tests it.
- **Outbound rate limit:** requests and tokens per minute per route, from configuration. There
  are two backends behind one interface:
  - in memory (default; eval and a single worker);
  - Postgres (shared by every worker process that uses the same key).
- Spans show throttle waits and skipped routes.
- **Idempotent model calls on resume: not built.** The graph checkpoints after every node, and
  each node calls the model at most once. A resume therefore repeats at most the one call in
  flight, which has no side effect but its cost.
  `test_a_killed_run_resumes_without_repeating_finished_model_turns` already proves the rest.
  This is recorded in the ADR.
- **Other non-goals:**
  - a shared breaker;
  - reading `x-ratelimit-*` response headers (a later add-on);
  - a Postgres limiter for eval runs;
  - ops dashboard panels.
- **Anti-goals:**
  - A limiter outage that stops model calls. The Postgres limiter fails open, because the
    provider's 429 stays the last safety net.
  - A breaker tripped by the model's own mistakes (`InvalidToolCallError`) or by permanent
    errors.
  - A breaker tripped by our own throttling.

## Approaches

| Option | Trade-off | Decision |
|---|---|---|
| A. All state in process | No latency or new failure point; per-route limits are wrong with N processes unless split by hand | Kept for the breaker, and as the default limiter |
| B. All state in Postgres | Limits right across processes; one DB round trip per call and a new failure point; eval runs often have no DB | Kept for the limiter only, enabled by configuration |
| Combined: breaker in process, limiter behind an interface with both backends | Industry practice: breakers are per instance (Hystrix, Resilience4j, Envoy outlier detection); quotas are global (Envoy global rate limit, LiteLLM with Redis) | **Chosen** by the user, 2026-11-20 |

## Design

### Breaker (`da_agent.llm.breaker`)

- **States:** closed, open, half-open. Defaults: 5 consecutive failed attempts and a 30-second
  cooldown. After the cooldown, exactly one caller gets a probe. A success closes the breaker; a
  failure opens it again.
- **What counts:** only `RateLimitedError` and `TransientLLMError` attempts. Any success resets
  the count. Permanent errors, invalid tool calls, and our own throttling never count.
- **In `LLMClient`:**
  - A route whose breaker refuses is skipped, with "circuit open" in the failure list and a span
    event.
  - When the breaker opens during retries, retrying that route stops.
  - When every route is open, the call ends at once with `AllRoutesFailedError`. A worker run job
    is then retried later; an eval question stays open.

### Rate limiter (`da_agent.llm.ratelimit`)

- **Interface:**
  - `acquire(route_key, tokens, max_wait) -> float` returns the seconds to wait. It raises
    `ThrottledError` when the wait would exceed `max_wait`.
  - `settle(route_key, reserved, used)` corrects the token count after the call.
- **Algorithm:** two token buckets per route, one for requests and one for tokens.
  - Each bucket holds up to one minute's limit and refills continuously.
  - A request reserves its cost at once, and the balance may go negative: later callers queue
    behind it, and wait is the deficit divided by the refill rate.
  - A request whose wait would exceed `max_wait` reserves nothing and raises.
  - A cost above the capacity is capped at the capacity, so a large request never waits forever.
- **Token reservation:** an estimate of the request size, plus its `max_output_tokens`. The
  estimate is the characters of the messages and tools divided by 4. After the call, `settle`
  replaces the reservation with the real input and output tokens. A failed call settles to zero.
- **`ThrottledError`:** a `RateLimitedError` subclass, so `LLMClient` falls back to the next
  route. It never counts toward the breaker, is not retried on the same route, and is recorded in
  the ledger as error kind `throttled`.
- **Postgres backend:**
  - Table `rate_buckets (route_key, kind, level, updated_at)`.
  - Each `acquire` is one transaction: the requests row first, then the tokens row, always in
    that order so two callers never deadlock. Each statement is an upsert that refills from
    `now()` (the database clock, so machine clocks do not matter) and reserves only while the
    wait stays within `max_wait`. If either row refuses, the transaction rolls back.
  - It uses its own connection. On a database error it logs to the span and lets the call
    through (fail open), then reconnects on the next call.

### Configuration

- `DA_AGENT_ROUTE_LIMITS`: JSON such as `{"groq:openai/gpt-oss-20b": {"rpm": 30, "tpm": 8000}}`.
  When it is unset, there are no limits.
- `DA_AGENT_RATE_LIMITER`: `memory` (default) or `postgres` (worker only).
- `DA_AGENT_BREAKER_FAILURES` (default 5) and `DA_AGENT_BREAKER_COOLDOWN_SECONDS` (default 30).
- The eval reads the same variables, always with the memory backend.

## Left to the builder

- Class and helper names beyond the interface above.
- Span attribute names, following the existing `llm.*` and `da_agent.*` style.
