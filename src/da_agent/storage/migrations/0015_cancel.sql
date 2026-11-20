-- Runs a user stops (ADR 0042). A queued job is cancelled at once; a running one records the
-- request, and its worker stops the run and ends the job cancelled.
ALTER TABLE jobs DROP CONSTRAINT jobs_status_check;
ALTER TABLE jobs ADD CONSTRAINT jobs_status_check
    CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled'));
ALTER TABLE jobs ADD COLUMN cancel_requested_at timestamptz;
