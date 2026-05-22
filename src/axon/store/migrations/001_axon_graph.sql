-- 001_axon_graph: graph, session, commit and decision tables for AXON.
-- payload/JSON columns are stored as TEXT; SQLite JSON1 functions operate on them.

PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS nodes (
    id          TEXT    PRIMARY KEY,
    type        TEXT    NOT NULL,
    label       TEXT    NOT NULL DEFAULT '',
    payload     TEXT    NOT NULL DEFAULT '{}',
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS edges (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id   TEXT    NOT NULL REFERENCES nodes(id),
    target_id   TEXT    NOT NULL REFERENCES nodes(id),
    type        TEXT    NOT NULL,
    payload     TEXT,
    created_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id              TEXT    PRIMARY KEY,
    agent           TEXT    NOT NULL,
    repo            TEXT    NOT NULL,
    started_at      TEXT    NOT NULL,
    ended_at        TEXT,
    context_payload TEXT    NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS commits (
    hash            TEXT    PRIMARY KEY,
    repo            TEXT    NOT NULL,
    message         TEXT    NOT NULL,
    decisions_json  TEXT    NOT NULL DEFAULT '[]',
    timestamp       TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS decisions (
    id          TEXT    PRIMARY KEY,
    frontmatter TEXT    NOT NULL,
    body        TEXT    NOT NULL DEFAULT '',
    vault_path  TEXT,
    created_at  TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_nodes_type ON nodes(type);
CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_id);
CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_id);
CREATE INDEX IF NOT EXISTS idx_edges_type ON edges(type);
CREATE INDEX IF NOT EXISTS idx_decisions_created ON decisions(created_at);
CREATE INDEX IF NOT EXISTS idx_sessions_repo ON sessions(repo);
