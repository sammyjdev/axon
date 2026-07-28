-- 0003_hash_natural_key_indexes: replace the full-text natural-key UNIQUE
-- indexes from 0002 with md5() expression indexes (#111). Btree caps index
-- rows at 2704 bytes, so summaries/bodies above ~2.6KB of post-TOAST data
-- could never be inserted. Hashing the text keeps the natural-key dedup
-- contract (save_session_memory / save_note ON CONFLICT) without the cap.

DROP INDEX IF EXISTS uq_session_memory_natural;
CREATE UNIQUE INDEX IF NOT EXISTS uq_session_memory_natural
    ON session_memory (project, md5(summary), raw_turns, created_at);

DROP INDEX IF EXISTS uq_session_note_natural;
CREATE UNIQUE INDEX IF NOT EXISTS uq_session_note_natural
    ON session_note (project, md5(body), created_at);
