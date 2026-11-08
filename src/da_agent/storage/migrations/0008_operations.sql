-- For the operator dashboard (ADR 0026). started_at is set by the first claim and kept by retries,
-- so queue wait is started_at - created_at; finished_at is set when a job succeeds or fails for
-- good. Rows from before this migration have neither and are left out of timings.
ALTER TABLE jobs ADD COLUMN started_at timestamptz;
ALTER TABLE jobs ADD COLUMN finished_at timestamptz;
CREATE INDEX jobs_created ON jobs (created_at);

-- Failed model attempts: zero tokens and cost, and the kind of failure.
ALTER TABLE llm_calls ADD COLUMN error_kind text
    CHECK (error_kind IN ('rate_limited', 'transient', 'permanent', 'invalid_tool_call'));
CREATE INDEX llm_calls_created ON llm_calls (created_at);
