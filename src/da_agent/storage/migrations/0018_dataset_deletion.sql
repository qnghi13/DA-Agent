-- Deleting a dataset (docs/design/2026-11-24-dataset-deletion.md): a dataset being deleted has
-- status 'deleting', and the delete runs as a 'delete' job.
ALTER TABLE datasets DROP CONSTRAINT datasets_status_check;
ALTER TABLE datasets ADD CONSTRAINT datasets_status_check
    CHECK (status IN ('uploaded', 'proposed', 'building', 'ready', 'failed', 'deleting'));
ALTER TABLE jobs DROP CONSTRAINT jobs_kind_check;
ALTER TABLE jobs ADD CONSTRAINT jobs_kind_check
    CHECK (kind IN ('ingest', 'build', 'run', 'export', 'query', 'version', 'delete'));
