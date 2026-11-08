-- Browser sign-in (ADR 0025). Tokens are stored only as SHA-256 hashes. A member belongs to one
-- workspace; the operator belongs to none.
CREATE TABLE login_links (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    token_hash bytea NOT NULL UNIQUE,
    role text NOT NULL CHECK (role IN ('member', 'operator')),
    workspace_id uuid REFERENCES workspaces (id) ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    used_at timestamptz,
    CHECK ((role = 'operator') = (workspace_id IS NULL))
);

CREATE TABLE sessions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    token_hash bytea NOT NULL UNIQUE,
    role text NOT NULL CHECK (role IN ('member', 'operator')),
    workspace_id uuid REFERENCES workspaces (id) ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    revoked_at timestamptz,
    CHECK ((role = 'operator') = (workspace_id IS NULL))
);

CREATE INDEX sessions_workspace ON sessions (workspace_id) WHERE revoked_at IS NULL;

ALTER TABLE audit_log ADD COLUMN session_id uuid REFERENCES sessions (id) ON DELETE SET NULL;
