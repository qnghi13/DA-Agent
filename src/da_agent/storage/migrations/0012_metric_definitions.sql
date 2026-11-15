-- Metric definitions saved from an answer (docs/design/2026-11-15-metric-definitions.md). A metric
-- is a memory entry with a name, other names, and the measure and row filters read from a query.
ALTER TABLE dataset_memories DROP CONSTRAINT dataset_memories_kind_check;
ALTER TABLE dataset_memories ADD CONSTRAINT dataset_memories_kind_check
    CHECK (kind IN ('correction', 'alias', 'example', 'metric'));

ALTER TABLE dataset_memories
    ADD COLUMN name text CHECK (length(btrim(name)) BETWEEN 1 AND 80),
    ADD COLUMN aliases text[] CHECK (cardinality(aliases) <= 10),
    ADD COLUMN definition jsonb,
    ADD CONSTRAINT dataset_memories_metric_check
        CHECK (kind <> 'metric' OR (name IS NOT NULL AND definition IS NOT NULL));
