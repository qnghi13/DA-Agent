-- Feedback on runs and the golden cases a thumbs-up makes (ADR 0043). One rating per run; reasons
-- and the note are kept only with a thumbs-down. A golden case keeps the run's question and answer
-- as the expected one, and is retired, not deleted, when the thumbs-up goes away.
CREATE TABLE run_feedback (
    job_id uuid PRIMARY KEY REFERENCES jobs (id) ON DELETE CASCADE,
    workspace_id uuid NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    rating text NOT NULL CHECK (rating IN ('up', 'down')),
    reasons text[] NOT NULL DEFAULT '{}' CHECK (
        reasons <@ ARRAY['wrong_number', 'misunderstood', 'should_abstain', 'unclear', 'other']
    ),
    note text CHECK (length(note) BETWEEN 1 AND 1000),
    author text NOT NULL CHECK (length(author) BETWEEN 1 AND 200),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (rating = 'down' OR (reasons = '{}' AND note IS NULL))
);
CREATE INDEX run_feedback_by_time ON run_feedback (workspace_id, updated_at);

CREATE TABLE golden_cases (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    dataset_id uuid NOT NULL,
    source_run_id uuid NOT NULL UNIQUE REFERENCES jobs (id) ON DELETE CASCADE,
    question text NOT NULL,
    as_of text NOT NULL,
    expected jsonb NOT NULL,
    active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    retired_at timestamptz
);
CREATE INDEX golden_cases_active ON golden_cases (workspace_id, dataset_id) WHERE active;
