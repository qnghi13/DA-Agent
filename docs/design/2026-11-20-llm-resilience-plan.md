# Circuit breaker and outbound rate limits: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a per-process circuit breaker and per-route request and token limits to `LLMClient`.
Limits have an in-memory backend and a Postgres backend.

**Architecture:**
- New modules `da_agent.llm.breaker` and `da_agent.llm.ratelimit`, both with an injected clock.
- `LLMClient` checks the breaker and the limiter before each attempt, and reports outcomes to both
  after it.
- The worker and the eval build both from environment settings.

**Tech Stack:** Python 3.12, psycopg 3, Postgres, OpenTelemetry, pytest.

**Spec:** [2026-11-20-llm-resilience.md](2026-11-20-llm-resilience.md)

## Global Constraints

- **Tests first.** Unit tests use fake clocks and sleeps; they never sleep for real or call a
  model. Postgres tests are integration tests that need `DA_AGENT_TEST_POSTGRES_URL`.
- **Defaults:**
  - breaker: 5 failures, 30 s cooldown;
  - limiter: off unless `DA_AGENT_ROUTE_LIMITS` is set;
  - backend: `memory`.
- **The breaker counts only** `RateLimitedError` (except `ThrottledError`) and
  `TransientLLMError`.
- **The Postgres limiter fails open** on `psycopg.Error`.
- **No new dependencies.**

## Review Focus

1. Many eval worker threads on one route, with an rpm limit: no burst beyond the limit, no
   deadlock, and waits that grow fairly.
2. Two Postgres limiter processes sharing one quota: together they never reserve more than the
   bucket allows.
3. When the breaker opens during a request's retries, retrying stops and the call falls back at
   once.
4. While half-open, two threads arrive together: only one probe goes out.
5. When the limiter database goes down, model calls continue, with an error on the span.

---

### Task 1: Circuit breaker

**Files:** Create `src/da_agent/llm/breaker.py`; Test `tests/unit/test_llm_breaker.py`.

**Interfaces:**
- **Produces:**
  - `BreakerPolicy(failures: int = 5, cooldown_seconds: float = 30.0)`, a frozen dataclass.
  - `CircuitBreaker(policy, clock=time.monotonic)` with:
    - `allow() -> bool`
    - `record_success() -> None`
    - `record_failure() -> None`
    - `state: Literal["closed", "open", "half_open"]`

- [ ] **Step 1: Write the tests.**
  - It opens after 5 consecutive failures, and not after 4 followed by a success.
  - `allow()` is False while open, and True once after the cooldown (half-open). A second
    caller gets False.
  - A successful probe closes the breaker.
  - A failed probe reopens it for another full cooldown.
  - With 8 threads calling `allow()` at the half-open moment, exactly one gets True.
- [ ] **Step 2: Run the tests.** `uv run pytest tests/unit/test_llm_breaker.py` fails, because
  the module is missing.
- [ ] **Step 3: Implement**, with a `threading.Lock`.
- [ ] **Step 4: Run the tests.** They pass.

### Task 2: Breaker inside LLMClient

**Files:** Modify `src/da_agent/llm/client.py`; Test `tests/unit/test_llm_client.py`.

**Interfaces:**
- **Consumes:** Task 1.
- **Produces:** `LLMClient(..., breaker_policy: BreakerPolicy | None = BreakerPolicy())`, with
  one breaker per route index. `None` turns it off.

- [ ] **Step 1: Write the tests.**
  - Two routes. The primary fails with transient errors across requests, until it opens. The
    next request calls the fallback only, so the primary's call count is unchanged and nothing
    is slept.
  - With every route open, the call raises `AllRoutesFailedError` mentioning "circuit open",
    without calling any adapter.
  - When the breaker opens mid-retry (policy `failures=2`, `max_retries=5`), the route stops
    after 2 attempts.
  - `InvalidToolCallError` and `PermanentLLMError` never open it.
  - The span of a skipped route carries the event `circuit_open`.
- [ ] **Step 2: Run the tests.** They fail.
- [ ] **Step 3: Implement:**
  - Check `allow()` before each attempt.
  - Record success, or a counted failure, after each attempt.
  - Stop retrying when `allow()` turns False.
- [ ] **Step 4: Run the whole unit suite.** It passes. Existing tests that script long runs of
  transient failures must still pass with the default policy. If one cannot, record a ruling and
  do not weaken its assertion.

### Task 3: In-memory rate limiter inside LLMClient

**Files:**
- Create `src/da_agent/llm/ratelimit.py`.
- Modify `src/da_agent/llm/client.py` and `src/da_agent/llm/types.py`.
- Test `tests/unit/test_llm_ratelimit.py` and `tests/unit/test_llm_client.py`.

**Interfaces:**
- **Produces:**
  - `RouteLimit(rpm: int | None = None, tpm: int | None = None)`.
  - `ThrottledError(RateLimitedError)` in `types.py`.
  - The protocol `RateLimiter`, with:
    - `acquire(route_key: str, tokens: int, max_wait: float) -> float`
    - `settle(route_key: str, reserved: int, used: int) -> None`
  - `MemoryRateLimiter(limits: dict[str, RouteLimit], clock=time.monotonic)`.
  - `estimate_tokens(request: LLMRequest) -> int`.
  - `route_key(provider, model) -> str`, returning `"provider:model"`.
  - `LLMClient(..., limiter: RateLimiter | None = None)`.

- [ ] **Step 1: Write the limiter tests**, with a fake clock:
  - With rpm 60, 60 requests pass at once; the 61st waits 1.0 s.
  - With tpm 6000, a 3000-token reservation followed by a 4000-token one waits 10 s.
  - A wait above `max_wait` raises `ThrottledError` and reserves nothing, so the next small
    request does not wait longer.
  - A cost above the capacity is capped.
  - `settle(key, 4000, 1000)` returns 3000 tokens.
  - A key without limits never waits.
- [ ] **Step 2: Write the client tests.**
  - The client sleeps for the wait the limiter returns, then calls.
  - A `ThrottledError` falls back to route 2 without counting toward route 1's breaker.
  - After a call, `settle` receives the real usage; after a failed call, it receives 0.
  - The ledger records error kind `throttled`.
  - The span carries `llm.throttle_wait_seconds`.
- [ ] **Step 3: Run the tests.** They fail.
- [ ] **Step 4: Implement.**
- [ ] **Step 5: Run the tests.** They pass.

### Task 4: Postgres rate limiter

**Files:**
- Create `src/da_agent/storage/migrations/0014_rate_buckets.sql` and
  `src/da_agent/llm/pg_ratelimit.py`.
- Test `tests/integration/test_pg_ratelimit.py`.

**Interfaces:**
- **Consumes:** Task 3's `RateLimiter` protocol and `RouteLimit`.
- **Produces:**
  `PostgresRateLimiter(limits, connect: Callable[[], psycopg.Connection], tracer=None)`.

- [ ] **Step 1: Write the integration tests.**
  - Two limiter instances on separate connections, with rpm 10. Twelve acquires in total: the
    first 10 wait about 0 and the 11th waits about 6 s.
  - When the tokens bucket refuses, the requests row is rolled back.
  - When `connect` raises `psycopg.OperationalError`, `acquire` returns 0 and the span records
    the error.
  - `settle` refunds tokens.
- [ ] **Step 2: Run the tests.** With
  `uv run --env-file .env pytest tests/integration/test_pg_ratelimit.py`, they fail.
- [ ] **Step 3: Implement** the migration and the limiter: one transaction, requests row before
  tokens row, refill from `now()`.
- [ ] **Step 4: Run the tests.** They pass.

### Task 5: Configuration and wiring

**Files:**
- Modify `src/da_agent/config.py`, `src/da_agent/worker/__main__.py`, `eval/datamate/run.py`,
  `eval/datamate/prepare.py`, and `eval/ingestion/prepare.py` where they build `LLMClient`.
- Test `tests/unit/test_config.py`, `tests/unit/test_entrypoints.py`, and
  `tests/unit/test_datamate_eval.py`.

**Interfaces:**
- **Produces:** `resilience_from_env(env) -> tuple[BreakerPolicy, dict[str, RouteLimit]]` in
  `da_agent.llm.factory`. Settings gains `route_limits`, `rate_limiter`, and `breaker_policy`.

- [ ] **Step 1: Write the tests.**
  - Parsing `DA_AGENT_ROUTE_LIMITS`, including bad JSON, an unknown field, and a non-positive
    value, each giving a clear `ConfigError` or `ValueError`.
  - The breaker variables.
  - A worker built with `DA_AGENT_RATE_LIMITER=postgres` uses `PostgresRateLimiter`.
  - The eval builds `MemoryRateLimiter` when limits are set.
- [ ] **Step 2: Run the tests.** They fail.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the tests.** They pass.

### Task 6: Docs

- [ ] ADR 0041. It covers the decisions, why resume idempotency is not built (one model call per
  node, plus the existing test), the rejected options, and the consequences.
- [ ] Learning note 20 (Vietnamese).
- [ ] Indexes, `architecture.md` (an LLM layer paragraph), `progress.md`, and `.env.example` if
  present.

### Final

- Run `uv run --env-file .env pytest` and `uv run ruff check .`.
- Then a fresh whole-branch review on the most capable model.
- An optional real check, with your go-ahead: 3 Groq questions with `rpm 2`, to see throttle waits
  in `metrics.json` and the spans. It costs about $0.006.
