-- Conversation and dataset memory (ADR 0030). People write memory; a run reads only approved
-- entries that are not superseded. An edit adds a new entry and points the old one at it.
CREATE TABLE conversations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    dataset_id uuid NOT NULL REFERENCES datasets (id) ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX conversations_workspace ON conversations (workspace_id, created_at DESC);

-- Kept in step with payload->>'conversation_id' by JobQueue, so turns are found by index.
ALTER TABLE jobs ADD COLUMN conversation_id uuid REFERENCES conversations (id) ON DELETE SET NULL;
CREATE INDEX jobs_conversation ON jobs (conversation_id, created_at)
    WHERE conversation_id IS NOT NULL;

CREATE TABLE dataset_memories (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    dataset_id uuid NOT NULL REFERENCES datasets (id) ON DELETE CASCADE,
    kind text NOT NULL CHECK (kind IN ('correction', 'alias', 'example')),
    text text NOT NULL CHECK (length(btrim(text)) BETWEEN 1 AND 2000),
    sql text CHECK (length(sql) <= 10000),
    status text NOT NULL CHECK (status IN ('proposed', 'approved', 'rejected')),
    author text NOT NULL CHECK (length(author) BETWEEN 1 AND 200),
    source_run_id uuid REFERENCES jobs (id) ON DELETE SET NULL,
    superseded_by uuid REFERENCES dataset_memories (id) ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (kind <> 'example' OR sql IS NOT NULL)
);

CREATE INDEX dataset_memories_dataset ON dataset_memories (dataset_id, status, created_at);
