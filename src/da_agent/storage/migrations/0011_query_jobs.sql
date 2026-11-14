-- Edited SQL run as a job (docs/design/2026-11-14-edit-sql.md).
ALTER TABLE jobs DROP CONSTRAINT jobs_kind_check;
ALTER TABLE jobs ADD CONSTRAINT jobs_kind_check
    CHECK (kind IN ('ingest', 'build', 'run', 'export', 'query'));
