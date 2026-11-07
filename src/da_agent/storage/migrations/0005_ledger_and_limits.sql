-- Every model call, durable across workers, so run budgets survive retries and daily spending can
-- be summed per workspace. workspace_id is set when run_id is a job id.
CREATE TABLE llm_calls (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id text NOT NULL,
    workspace_id uuid REFERENCES workspaces (id) ON DELETE CASCADE,
    provider text NOT NULL,
    model text NOT NULL,
    input_tokens integer NOT NULL,
    output_tokens integer NOT NULL,
    cached_input_tokens integer NOT NULL,
    cost_usd double precision NOT NULL,
    latency_seconds double precision NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX llm_calls_run ON llm_calls (run_id);
CREATE INDEX llm_calls_workspace_day ON llm_calls (workspace_id, created_at);

-- Fixed-window request counters, shared by every API process.
CREATE TABLE rate_counters (
    bucket text NOT NULL,
    window_start timestamptz NOT NULL,
    hits integer NOT NULL,
    PRIMARY KEY (bucket, window_start)
);

-- Overrides the operator's default daily model budget for one workspace.
ALTER TABLE workspaces ADD COLUMN daily_budget_usd double precision
    CHECK (daily_budget_usd IS NULL OR daily_budget_usd >= 0);
