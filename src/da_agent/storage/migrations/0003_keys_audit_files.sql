-- API keys are stored as a SHA-256 hash of the whole key; the prefix finds the row.
CREATE TABLE api_keys (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    prefix text NOT NULL UNIQUE,
    key_hash bytea NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    revoked_at timestamptz
);

-- Who did what. workspace_id is empty for requests that failed authentication.
CREATE TABLE audit_log (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    workspace_id uuid REFERENCES workspaces (id) ON DELETE CASCADE,
    key_id uuid REFERENCES api_keys (id) ON DELETE SET NULL,
    action text NOT NULL,
    target text,
    detail jsonb NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX audit_log_workspace ON audit_log (workspace_id, created_at DESC);

-- Uploaded files: the original name, the stored name, and the size.
ALTER TABLE datasets ADD COLUMN files jsonb NOT NULL DEFAULT '[]';
