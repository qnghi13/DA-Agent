-- A dataset moves uploaded -> proposed (ingest job) -> ready (approved), or building (an edited
-- spec waits for its build job) -> ready, or failed. Only a ready dataset can be queried.
CREATE TABLE datasets (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    name text NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
    description text NOT NULL DEFAULT '' CHECK (length(description) <= 20000),
    status text NOT NULL DEFAULT 'uploaded'
        CHECK (status IN ('uploaded', 'proposed', 'building', 'ready', 'failed')),
    spec jsonb,
    summary jsonb NOT NULL DEFAULT '{}',
    documents jsonb NOT NULL DEFAULT '[]',
    error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX datasets_workspace ON datasets (workspace_id, created_at DESC);

ALTER TABLE jobs DROP CONSTRAINT jobs_kind_check;
ALTER TABLE jobs ADD CONSTRAINT jobs_kind_check CHECK (kind IN ('ingest', 'build', 'run'));
