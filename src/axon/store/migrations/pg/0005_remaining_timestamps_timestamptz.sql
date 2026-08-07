-- 0005_remaining_timestamps_timestamptz: the timestamp columns 0004 did not
-- reach (DEBT-1, follow-up to #31 / MS-1). Same defect, same fix: ISO text sorts
-- correctly only while every value happens to be uniform-UTC.
--
-- Includes schema_version.applied_at - the migration runner's own bookkeeping
-- carried the very defect these migrations exist to remove. The runner writes
-- that row in the same transaction as this file, so pg_migrations.py passes a
-- datetime from this version on; older code writing an ISO string against the
-- new column would fail loudly rather than silently, which is the right way
-- round.
--
-- Pin the session TimeZone first, as 0004 did: a value stored WITHOUT an offset
-- would otherwise be read in the server's local zone, shifting the instant while
-- row counts stay identical.
SET LOCAL TimeZone = 'UTC';

-- ALTER TABLE IF EXISTS is load-bearing, not defensive habit. SessionStore
-- creates its repositories lazily, so whichever one is touched first runs
-- ensure_schema first; a database that has only ever used sessions has no
-- `nodes` or `file_index` yet when this runs. Tables created later get the right
-- type from the inline DDL in their own repository.
ALTER TABLE IF EXISTS schema_version
    ALTER COLUMN applied_at TYPE timestamptz USING applied_at::timestamptz;

ALTER TABLE IF EXISTS nodes
    ALTER COLUMN created_at TYPE timestamptz USING created_at::timestamptz,
    ALTER COLUMN updated_at TYPE timestamptz USING updated_at::timestamptz;

ALTER TABLE IF EXISTS edges
    ALTER COLUMN created_at TYPE timestamptz USING created_at::timestamptz;

ALTER TABLE IF EXISTS decisions
    ALTER COLUMN created_at TYPE timestamptz USING created_at::timestamptz;

ALTER TABLE IF EXISTS adr
    ALTER COLUMN created_at TYPE timestamptz USING created_at::timestamptz;

ALTER TABLE IF EXISTS file_index
    ALTER COLUMN indexed_at TYPE timestamptz USING indexed_at::timestamptz;

ALTER TABLE IF EXISTS failure_record
    ALTER COLUMN created_at TYPE timestamptz USING created_at::timestamptz;

ALTER TABLE IF EXISTS outcome_record
    ALTER COLUMN created_at TYPE timestamptz USING created_at::timestamptz;
