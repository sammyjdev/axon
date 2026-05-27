-- 002_unique_edges: dedupe edges and enforce uniqueness on (source_id, target_id, type).
-- Idempotent capture retries used to insert the same `touches` edge multiple
-- times because there was no constraint preventing it.

-- 1. Collapse any pre-existing duplicates: keep the lowest-id row per triple.
DELETE FROM edges
WHERE id NOT IN (
    SELECT MIN(id) FROM edges GROUP BY source_id, target_id, type
);

-- 2. Add the constraint as a unique index (cheap to create on a deduped table).
CREATE UNIQUE INDEX IF NOT EXISTS ux_edges_triple
    ON edges (source_id, target_id, type);
