-- 0004_session_timestamps_timestamptz: session timestamps text -> timestamptz
-- (MS-1 / #31). Text columns sort correctly only while every value happens to be
-- uniform-UTC ISO; one offset-shifted write and ORDER BY DESC returns the wrong
-- row. timestamptz is the same 8 bytes, normalises to UTC, sorts as an integer.
--
-- Pin the session TimeZone first: a value stored WITHOUT an offset would
-- otherwise be interpreted in the server's local zone, silently shifting the
-- instant. Every value AXON writes is UTC-aware (datetime.now(UTC).isoformat()),
-- so UTC is the correct reading for any naive leftovers too. SET LOCAL is scoped
-- to the migration transaction and does not leak to other sessions.
SET LOCAL TimeZone = 'UTC';

-- The natural-key UNIQUE indexes from 0003 cover created_at; Postgres rebuilds
-- them as part of ALTER TYPE, preserving the ON CONFLICT dedup contract in
-- save_session_memory / save_note.
ALTER TABLE session_memory
    ALTER COLUMN created_at TYPE timestamptz USING created_at::timestamptz;

ALTER TABLE session_note
    ALTER COLUMN created_at TYPE timestamptz USING created_at::timestamptz;

ALTER TABLE code_change
    ALTER COLUMN changed_at TYPE timestamptz USING changed_at::timestamptz;

ALTER TABLE sessions
    ALTER COLUMN started_at TYPE timestamptz USING started_at::timestamptz,
    ALTER COLUMN ended_at   TYPE timestamptz USING ended_at::timestamptz;

-- NOT NULL is inherited from the baseline DDL on every column except
-- sessions.ended_at, which is nullable by design (open sessions).

-- Follow-up, deliberately out of scope here (#31 acceptance criteria):
-- schema_version.applied_at, plus the graph / decisions / file_index timestamp
-- columns, are still text. They are written by different subsystems with their
-- own migration paths.
