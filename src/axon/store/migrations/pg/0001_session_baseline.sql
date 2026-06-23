-- 0001_session_baseline: session tables that predate the Postgres migration
-- runner (MS-4 / #30). Extracted verbatim from the former
-- PostgresSessionRepository.ensure_schema inline DDL so existing databases keep
-- identical table shapes. IF NOT EXISTS keeps this safe on pre-existing tables.

CREATE TABLE IF NOT EXISTS session_memory (
    id          bigserial PRIMARY KEY,
    project     text    NOT NULL,
    summary     text    NOT NULL,
    raw_turns   integer NOT NULL,
    created_at  text    NOT NULL
);

CREATE TABLE IF NOT EXISTS session_note (
    id          bigserial PRIMARY KEY,
    project     text NOT NULL,
    body        text NOT NULL,
    created_at  text NOT NULL
);

CREATE TABLE IF NOT EXISTS code_change (
    commit_hash  text NOT NULL,
    file_path    text NOT NULL,
    diff_summary text NOT NULL,
    why          text NOT NULL DEFAULT '',
    changed_at   text NOT NULL,
    PRIMARY KEY (commit_hash, file_path)
);

CREATE TABLE IF NOT EXISTS sessions (
    id              text PRIMARY KEY,
    agent           text NOT NULL,
    repo            text NOT NULL,
    started_at      text NOT NULL,
    ended_at        text,
    context_payload text NOT NULL DEFAULT '{}'
);
