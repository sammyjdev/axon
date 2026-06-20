-- 003_file_index.sql
-- Persistent per-file hash cache for cross-process incremental skip.
-- executescript() emits an implicit COMMIT before executing; DDL with
-- IF NOT EXISTS makes re-execution safe (idempotent).
-- status: 'pending' = Qdrant mutation in progress (crash sentinel)
--         'done'    = chunks successfully flushed to Qdrant

CREATE TABLE IF NOT EXISTS file_index (
    file_path   TEXT    NOT NULL,
    ctx         TEXT    NOT NULL,
    sha1        TEXT    NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'done',
    chunk_count INTEGER NOT NULL DEFAULT 0,
    indexed_at  TEXT    NOT NULL,
    PRIMARY KEY (file_path, ctx)
);

CREATE INDEX IF NOT EXISTS ix_file_index_ctx
    ON file_index (ctx);

CREATE INDEX IF NOT EXISTS ix_file_index_status
    ON file_index (status);
