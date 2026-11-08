-- The OpenTelemetry trace of each attempt, in attempt order, so an operator can open every try of
-- a job in the trace viewer (ADR 0023).
ALTER TABLE jobs ADD COLUMN trace_ids text[] NOT NULL DEFAULT '{}';

-- Listing a dataset's runs, newest first.
CREATE INDEX jobs_runs_by_dataset ON jobs (workspace_id, (payload ->> 'dataset_id'), created_at DESC)
    WHERE kind = 'run';
