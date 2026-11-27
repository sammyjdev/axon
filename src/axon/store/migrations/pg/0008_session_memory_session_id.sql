-- 0008_session_memory_session_id (#187): one session_memory row per harness
-- session. Additive: no existing row is changed, and rows saved before this
-- keep session_id NULL (they carry no reliable session identity).
--
-- A keyed row keeps the greatest compact summary and the greatest digest it has
-- received; summary, raw_turns and created_at show the one picked from the two.
--
-- NOT backward compatible with older writers. The natural index becomes partial,
-- so the older "ON CONFLICT (project, md5(summary), raw_turns, created_at)", with
-- no predicate, no longer matches it: code from before this file fails every
-- session_memory save once it is applied. Do not downgrade the code only, and do
-- not recreate the full unique index. Upgrade and recovery steps:
-- docs/USAGE_GUIDE.md, "Upgrading past migration 0008".

ALTER TABLE session_memory
    ADD COLUMN IF NOT EXISTS session_id text,
    ADD COLUMN IF NOT EXISTS compact_summary text,
    ADD COLUMN IF NOT EXISTS compact_created_at timestamptz,
    ADD COLUMN IF NOT EXISTS digest_summary text,
    ADD COLUMN IF NOT EXISTS digest_raw_turns integer,
    ADD COLUMN IF NOT EXISTS digest_created_at timestamptz;

CREATE UNIQUE INDEX IF NOT EXISTS uq_session_memory_session_id
    ON session_memory (session_id) WHERE session_id IS NOT NULL;

-- The content key now dedups only rows without a session id, so two sessions
-- with identical content do not collide on it.
DROP INDEX IF EXISTS uq_session_memory_natural;
CREATE UNIQUE INDEX uq_session_memory_natural
    ON session_memory (project, md5(summary), raw_turns, created_at)
    WHERE session_id IS NULL;
