# dec-136: operational activity history is durable evidence, not semantic memory

- Status: Accepted
- Date: 2026-09-15
- Relates to: dec-121 (single Postgres backend), dec-109 (restricted-context write
  gate), the AXON event-based memory-capture pipeline

## Decision

Operational activity (Claude Code, Codex, and AGY session/turn/tool-call
records) is stored as a separate PostgreSQL subsystem: `activity_sessions`,
`activity_events`, `activity_cursors`, `activity_evidence_links`. It is
**durable evidence of what a harness observably did**, not semantic memory.
It is never summarized into `SessionStore`, never fed into `recall_context`
ranking, and never ingested as a decision or lesson on its own.

## Boundary rules

1. **Separation from memory.** Activity history is durable evidence, not
   semantic memory. Existing AXON memory, recall, and decision records may
   reference activity only through optional explicit IDs
   (`activity_evidence_links`), never implicitly.
2. **Explicit-link rule.** A link between an existing AXON record and an
   activity event requires an explicit identifier supplied by the caller or
   adapter. Recalled context delivered to a session is recorded only as
   `context-delivered` evidence; AXON never infers that the delivered context
   was used just because it was returned. Shared workspace path or timestamp
   proximity alone never creates a link.
3. **No timestamp causality.** Native identifiers (session id, turn id, call
   id) order and correlate records. Timestamps may order events for display
   but they cannot prove causality between them, and are never used to infer
   a call/result or delegation relationship.
4. **Retention.** No automatic deletion or retention scheduler exists in v1.
   Sanitized activity persists until an explicit deletion feature is
   separately designed.
5. **Redaction.** Every event is sanitized before it reaches a spool file,
   PostgreSQL row, warning log, or API/dashboard response. Credential-shaped
   values, named secret fields, environment dumps, opaque encrypted
   reasoning, and restricted `work` content are excluded by default and
   replaced with an explicit redaction marker and reason.
6. **Coverage.** Every event and session carries an explicit coverage marker
   (for example `complete-observable`, `partial-observable`, `redacted`,
   `unsupported-format`). Unknown-but-safe fields are preserved under a
   coverage marker rather than guessed at or silently dropped.
7. **Local-only.** This is a personal, local-only subsystem: no team access,
   no multi-host coordination, no external observability export (OTLP or
   otherwise), and no product telemetry in v1.
8. **v1 exclusions.** No periodic LLM calls, no semantic-memory ingestion of
   terminal events, no synthetic summaries, no automatic expiry, no cost
   estimates, no team access, no multi-host support, no OTLP export, no
   product telemetry, and no IDE-only capture in v1.

## Rollback

Disabling activity collection stops new ingestion immediately, but it does not delete existing sanitized activity already stored in PostgreSQL or pending in
the local spool; that data remains queryable until an explicit deletion
feature is separately designed and run.

## Consequences

- Activity tables are additive: no existing `sessions` table, memory-capture
  path, or recall ranking changes because this subsystem exists.
- Every adapter (Task 5-7) must sanitize and mark coverage before a record is
  durable, or the record must not be reported as captured.
- Provenance links (Task 8) are opt-in and explicit; the absence of a link
  is the default and valid state for all existing memory records.
