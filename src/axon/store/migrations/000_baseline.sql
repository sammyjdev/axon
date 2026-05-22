-- 000_baseline: tables that predate the AXON migration runner.
-- Extracted verbatim from the former SessionStore.DDL constant.

CREATE TABLE IF NOT EXISTS adr (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project     TEXT    NOT NULL,
    title       TEXT    NOT NULL,
    context     TEXT    NOT NULL,
    decision    TEXT    NOT NULL,
    rationale   TEXT    NOT NULL,
    created_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS session_memory (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project     TEXT    NOT NULL,
    summary     TEXT    NOT NULL,
    raw_turns   INTEGER NOT NULL,
    created_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS code_change (
    commit_hash TEXT    NOT NULL,
    file_path   TEXT    NOT NULL,
    diff_summary TEXT   NOT NULL,
    why         TEXT    NOT NULL DEFAULT '',
    changed_at  TEXT    NOT NULL,
    PRIMARY KEY (commit_hash, file_path)
);

CREATE TABLE IF NOT EXISTS session_note (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    project    TEXT NOT NULL,
    body       TEXT NOT NULL,
    created_at TEXT NOT NULL
);
