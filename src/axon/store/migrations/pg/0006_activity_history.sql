-- Activity History (Task 2)
CREATE TABLE IF NOT EXISTS activity_sessions (
    session_id          text PRIMARY KEY,
    harness             text NOT NULL,
    source_id           text NOT NULL,
    project             text,
    workspace           text,
    parent_session_id   text,
    status              text NOT NULL,
    coverage            text NOT NULL,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    UNIQUE (harness, source_id)
);

CREATE TABLE IF NOT EXISTS activity_events (
    event_id      text PRIMARY KEY,
    schema_version integer NOT NULL DEFAULT 1,
    harness       text NOT NULL,
    source_id     text NOT NULL,
    session_id    text NOT NULL,
    turn_id       text,
    call_id       text,
    parent_session_id text,
    occurred_at   timestamptz NOT NULL,
    ingested_at   timestamptz NOT NULL,
    kind          text NOT NULL,
    content       jsonb NOT NULL,
    outcome       text,
    coverage      text NOT NULL,
    redactions    jsonb NOT NULL DEFAULT '[]',
    UNIQUE (harness, source_id)
);

CREATE INDEX IF NOT EXISTS idx_activity_events_session_order
    ON activity_events (session_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_activity_events_harness_outcome
    ON activity_events (harness, outcome);
CREATE INDEX IF NOT EXISTS idx_activity_events_search
    ON activity_events USING gin (content jsonb_path_ops);
CREATE INDEX IF NOT EXISTS idx_activity_sessions_project_harness
    ON activity_sessions (project, harness, created_at);

CREATE TABLE IF NOT EXISTS activity_cursors (
    harness      text NOT NULL,
    source_id    text NOT NULL,
    fingerprint  text NOT NULL,
    byte_offset  bigint NOT NULL DEFAULT 0,
    updated_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (harness, source_id)
);

CREATE TABLE IF NOT EXISTS activity_evidence_links (
    id           bigserial PRIMARY KEY,
    target_type  text NOT NULL,
    target_id    text NOT NULL,
    session_id   text NOT NULL,
    turn_id      text,
    event_id     text,
    relation     text NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_activity_evidence_target
    ON activity_evidence_links (target_type, target_id);
