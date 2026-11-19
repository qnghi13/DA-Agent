-- Which agent ran each job attempt (docs/design/2026-11-19-run-manifest.md, ADR 0038). A worker
-- registers its manifest once; every claim of a job writes one attempt row with that version.
CREATE TABLE agent_versions (
    id text PRIMARY KEY,
    manifest jsonb NOT NULL,
    first_seen timestamptz NOT NULL DEFAULT now()
);

-- resumed_from_version has no foreign key: a checkpoint may name a version written by a worker of
-- another database, or by an eval run.
CREATE TABLE job_attempts (
    job_id uuid NOT NULL REFERENCES jobs (id) ON DELETE CASCADE,
    attempt integer NOT NULL,
    worker_id text NOT NULL,
    agent_version text REFERENCES agent_versions (id),
    trace_id text,
    resumed_from_version text,
    started_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (job_id, attempt)
);

CREATE INDEX job_attempts_by_version ON job_attempts (agent_version);
