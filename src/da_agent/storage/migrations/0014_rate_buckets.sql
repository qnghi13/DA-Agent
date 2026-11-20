-- Outbound request and token buckets per model route, shared by worker processes (ADR 0041). A
-- row holds the level after its last refill; the limiter refills it from the database clock.
CREATE TABLE rate_buckets (
    route_key text NOT NULL,
    kind text NOT NULL CHECK (kind IN ('requests', 'tokens')),
    level double precision NOT NULL,
    updated_at timestamptz NOT NULL,
    PRIMARY KEY (route_key, kind)
);
