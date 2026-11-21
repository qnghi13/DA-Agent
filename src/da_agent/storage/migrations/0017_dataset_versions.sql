-- Adding files to a dataset: each upload is a new version (docs/design/2026-11-21-add-files.md).
-- uploaded -> checking -> (applied | needs_choice | proposed | failed | discarded);
-- needs_choice and proposed lead to building -> applied.
CREATE TABLE dataset_versions (
    dataset_id uuid NOT NULL REFERENCES datasets (id) ON DELETE CASCADE,
    version int NOT NULL CHECK (version >= 1),
    status text NOT NULL DEFAULT 'uploaded'
        CHECK (status IN ('uploaded', 'checking', 'needs_choice', 'proposed', 'building',
                          'applied', 'failed', 'discarded')),
    files jsonb NOT NULL DEFAULT '[]',
    upload_dir text NOT NULL,
    build_dir text,
    spec jsonb,
    summary jsonb,
    documents jsonb,
    tables jsonb,
    changes jsonb,
    choices jsonb,
    spec_edits jsonb,
    repairs jsonb,
    error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (dataset_id, version)
);

-- At most one open version per dataset.
CREATE UNIQUE INDEX dataset_versions_one_open ON dataset_versions (dataset_id)
    WHERE status NOT IN ('applied', 'failed', 'discarded');

ALTER TABLE datasets ADD COLUMN current_version int;

-- Every ready dataset gets version 1 from its current row; its folders keep the dataset id.
INSERT INTO dataset_versions
    (dataset_id, version, status, files, upload_dir, build_dir, spec, summary, documents)
SELECT id, 1, 'applied', files, id::text, id::text, spec, summary, documents
FROM datasets WHERE status = 'ready';
UPDATE datasets SET current_version = 1 WHERE status = 'ready';

ALTER TABLE jobs DROP CONSTRAINT jobs_kind_check;
ALTER TABLE jobs ADD CONSTRAINT jobs_kind_check
    CHECK (kind IN ('ingest', 'build', 'run', 'export', 'query', 'version'));
