-- Progress events of run jobs, read by the API's event stream in id order.
CREATE TABLE run_events (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    job_id uuid NOT NULL REFERENCES jobs (id) ON DELETE CASCADE,
    workspace_id uuid NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    type text NOT NULL CHECK (type ~ '^[a-z_]{1,32}$'),
    data jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX run_events_job ON run_events (job_id, id);
