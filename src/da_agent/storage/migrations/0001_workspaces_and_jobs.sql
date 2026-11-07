-- Workspaces own every dataset, job, and key. Jobs are the queue of ADR 0003.
CREATE TABLE workspaces (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name text NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE jobs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    kind text NOT NULL CHECK (kind IN ('ingest', 'run')),
    payload jsonb NOT NULL DEFAULT '{}',
    status text NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
    attempts integer NOT NULL DEFAULT 0,
    max_attempts integer NOT NULL DEFAULT 3 CHECK (max_attempts >= 1),
    run_after timestamptz NOT NULL DEFAULT now(),
    lease_until timestamptz,
    worker_id text,
    last_error text,
    result jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- Only open jobs are scanned when claiming.
CREATE INDEX jobs_claimable ON jobs (run_after) WHERE status IN ('queued', 'running');
CREATE INDEX jobs_workspace ON jobs (workspace_id, created_at DESC);
