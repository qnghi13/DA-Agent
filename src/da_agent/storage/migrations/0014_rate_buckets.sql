-- Outbound request and token buckets per model route, shared by worker processes (ADR 0041). A
-- row holds the level after its last refill; the limiter refills it from the database clock.
CREATE TABLE rate_buckets (
    route_key text NOT NULL,
    kind text NOT NULL CHECK (kind IN ('requests', 'tokens')),
    level double precision NOT NULL,
    updated_at timestamptz NOT NULL,
    PRIMARY KEY (route_key, kind)
);

-- A call our own limit refused is kept in the ledger with its own kind.
ALTER TABLE llm_calls DROP CONSTRAINT llm_calls_error_kind_check;
ALTER TABLE llm_calls ADD CONSTRAINT llm_calls_error_kind_check
    CHECK (error_kind IN ('rate_limited', 'transient', 'permanent', 'invalid_tool_call', 'throttled'));
