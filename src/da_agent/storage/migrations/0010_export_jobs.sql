-- Result export jobs (docs/design/2026-11-14-result-export.md).
ALTER TABLE jobs DROP CONSTRAINT jobs_kind_check;
ALTER TABLE jobs ADD CONSTRAINT jobs_kind_check
    CHECK (kind IN ('ingest', 'build', 'run', 'export'));
