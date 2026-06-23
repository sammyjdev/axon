-- 0002_natural_key_indexes: natural-key UNIQUE indexes on session_memory and
-- session_note, required for ON CONFLICT (natural-key) DO NOTHING RETURNING id
-- dedup in save_session_memory / save_note (#27). IF NOT EXISTS is idempotent.

CREATE UNIQUE INDEX IF NOT EXISTS uq_session_memory_natural
    ON session_memory (project, summary, raw_turns, created_at);

CREATE UNIQUE INDEX IF NOT EXISTS uq_session_note_natural
    ON session_note (project, body, created_at);
