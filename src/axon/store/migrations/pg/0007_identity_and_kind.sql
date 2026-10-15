-- 0007_identity_and_kind: a file is (repo, rel_path) and a chunk has a kind.
-- Additive: no vector is rewritten, no row is deleted, no existing file_path or
-- project value changes. `embeddings` and `file_index` are created in code
-- (ensure_collections / ensure_schema), never by a migration, so this file can
-- run before either exists - hence ALTER TABLE IF EXISTS, the same reason 0005
-- uses it. The matching in-code DDL lives in pg_vector_store.ensure_collections
-- and pg_file_cache.ensure_schema; change both together.

ALTER TABLE IF EXISTS embeddings
    ADD COLUMN IF NOT EXISTS kind text;

ALTER TABLE IF EXISTS file_index
    ADD COLUMN IF NOT EXISTS repo text NOT NULL DEFAULT '';

DO $$
DECLARE
    existing_pk text;
BEGIN
    IF to_regclass('file_index') IS NULL THEN
        RETURN;
    END IF;
    SELECT conname INTO existing_pk
      FROM pg_constraint
     WHERE conrelid = 'file_index'::regclass AND contype = 'p';
    IF existing_pk = 'file_index_repo_pkey' THEN
        RETURN;
    END IF;
    IF existing_pk IS NOT NULL THEN
        EXECUTE format('ALTER TABLE file_index DROP CONSTRAINT %I', existing_pk);
    END IF;
    ALTER TABLE file_index
        ADD CONSTRAINT file_index_repo_pkey PRIMARY KEY (repo, file_path, ctx);
END $$;
