# AXON Activity History and LLMOps - AGY Execution Plan

> **Executor:** AGY works one task at a time in a throwaway clone. It never commits,
> pushes, or receives an absolute path outside its current working directory. The
> host applies the reviewed diff, runs verification, and commits one accepted task.

**Goal:** Persist and search sanitized, observable Claude Code, Codex, and AGY
session history without changing AXON's event-based memory-capture behavior.

**Architecture:** Operational activity is a separate PostgreSQL subsystem. Every
sanitized event first lands in a durable local spool, then an idempotent repository
transaction stores events and source cursors together. Adapters only translate
observable source records into the common event contract. Existing AXON memory,
recall, and decision records reference activity only through optional explicit IDs.

**Tech stack:** Python 3.11, Pydantic v2, asyncpg/PostgreSQL, Typer, FastAPI,
vanilla HTML/JavaScript, pytest, existing AXON pending-spool conventions.

**Spec:** This document is self-contained. The original requested behavior is
captured below as executable work, acceptance metrics, and explicit non-goals.

## Why this is one document

The work has four delivery slices, but splitting it into separate documents would
lose the dependency chain that AGY needs. The slices remain independently
releasable through task gates:

| Slice | Tasks | Shippable result | Gate |
| --- | --- | --- | --- |
| Foundation | 0-4 | durable imported history and inspection/export | imported source may be deleted |
| Harnesses | 5-7 | Claude, Codex, and AGY capture with declared coverage | each controlled run has a timeline |
| Provenance | 8 | existing AXON records optionally link to observed activity | existing behavior is unchanged |
| Dashboard | 9-10 | authenticated exploration, search, health, and final restore proof | dashboard never invents unavailable data |

## Global constraints

- PostgreSQL is the only database. Do not add SQLite, Redis, an ORM, a queue,
  a frontend framework, or a runtime dependency on `claude-code-ops`.
- Keep `sessions` and activity sessions distinct. Join only through explicit
  foreign-key-like references, never timestamps or a shared workspace path.
- Sanitize before writing a spool file, PostgreSQL row, warning log, or response.
  Credentials, environment dumps, opaque encrypted reasoning, and restricted
  `work` content remain excluded by default.
- A successful source read is not durable capture. Report success only after the
  sanitized payload is in the spool. Report database failure and pending spool
  state visibly.
- Preserve native source order. Correlate calls and results by native identifiers;
  timestamps may order records but cannot prove causality.
- No periodic LLM calls, semantic-memory ingestion of terminal events, synthetic
  summaries, automatic expiry, cost estimates, team access, multi-host support,
  OTLP export, product telemetry, or IDE-only capture in v1.
- Dashboard content is inert DOM text. Do not use `innerHTML`, external CDNs, or
  unauthenticated routes. Reuse AXON's existing local bind and auth guard.
- Every task starts with the listed failing behavioral test. Do not modify an
  existing test merely to force a pass.

## AGY execution contract

For every task below, the orchestrator must create a fresh full clone of the
current accepted branch, place a task-specific brief inside that clone, and invoke
the existing `agy_run.py` PTY launcher. The brief must name files only by relative
path and say `the current working directory`, never an absolute path. AGY must:

1. Read the files named by the task and the relevant existing tests first.
2. Add the stated failing check, run it, then make the smallest implementation pass.
3. Touch only the files necessary for its task, add no dependency, and do not commit.
4. Return changed files, commands run, test output, and unresolved risks.

The host must inspect the diff, run the task verification itself, use a reviewer,
then commit. A failed host verification returns the same task to a new AGY run;
later tasks do not start.

### Standard task brief

```text
Implement Task <N> from docs/plans/2026-09-13-activity-history-llmops.md.
Work only in the current working directory. Do not name or access any path outside it.
Read the task's files and tests first. Start with its failing behavioral test.
Use no new dependency, do not commit or push, and do not change unrelated files.
At the end report: changed files, failing test observed, final commands/output,
and any requirement that could not be met.
```

## File and interface map

| Path | Responsibility | Produced by |
| --- | --- | --- |
| `docs/decisions/dec-136-operational-activity-history.md` | boundary between activity recording and memory capture | Task 0 |
| `src/axon/activity/models.py` | versioned Pydantic models and enums | Task 1 |
| `src/axon/activity/sanitize.py` | deterministic redaction and coverage markers | Task 1 |
| `src/axon/activity/spool.py` | sanitized, atomic, replayable local spool | Task 2 |
| `src/axon/activity/repository.py` | PostgreSQL writes, reads, cursor and evidence-link queries | Task 2 |
| `src/axon/activity/service.py` | idempotent ingest, collection, import, export, and search | Tasks 3-4 |
| `src/axon/activity/adapters/*.py` | one parser/collector per harness | Tasks 5-7 |
| `src/axon/store/migrations/pg/0006_activity_history.sql` | activity tables and indexes | Task 2 |
| `src/axon/cli/pb.py` | `activity` Typer group, no CLI refactor | Task 4 |
| `src/axon/mcp/server.py` and existing capture/recall code | optional explicit provenance IDs | Task 8 |
| `src/axon/http/app.py` | authenticated activity APIs | Task 9 |
| `src/axon/http/dashboard.py` | three dashboard views in vanilla JS | Task 10 |
| `tests/activity/` | activity behavioral fixtures and tests | Tasks 1-7 |
| `tests/http/test_activity_*.py` | API/dashboard contracts and XSS tests | Tasks 9-10 |

### Stable interfaces

These names are the contract across tasks. Do not rename them in a later task.

```python
class ActivityEvent(BaseModel):
    event_id: str
    schema_version: int = 1
    harness: Literal["claude-code", "codex", "agy"]
    source_id: str
    session_id: str
    turn_id: str | None = None
    call_id: str | None = None
    parent_session_id: str | None = None
    occurred_at: datetime
    ingested_at: datetime
    kind: str
    content: dict[str, object]
    outcome: str | None = None
    coverage: str
    redactions: list[str]

class ActivitySession(BaseModel):
    session_id: str
    harness: Literal["claude-code", "codex", "agy"]
    source_id: str
    project: str | None
    workspace: str | None
    parent_session_id: str | None = None
    status: str
    coverage: str

async def ingest_events(events: Sequence[ActivityEvent], *, cursor: SourceCursor) -> IngestResult: ...
async def search_activity(query: str, *, filters: ActivityFilters, limit: int, cursor: str | None) -> ActivityPage: ...
async def export_activity(session_id: str) -> AsyncIterator[str]: ...
```

`event_id` is deterministic from `(harness, source_id)` and is the replay key.
`content` contains only sanitized data. `coverage` is explicit, for example
`complete-observable`, `partial-observable`, `redacted`, or `unsupported-format`.
Every file-backed `SourceCursor` carries a source fingerprint as well as its byte
offset. On replacement or truncation, ingestion creates a new source generation,
re-reads safely, and emits a warning. Offset alone is never a cursor identity.

## Task 0: Record the architectural boundary

**Depends on:** none

**Files:** create `docs/decisions/dec-136-operational-activity-history.md`; modify
`docs/ADR.md` only if it is the project index's established registration point.

**Subtasks:**

- [ ] State that operational records are durable evidence, not semantic memory.
- [ ] State the explicit-link rule and the prohibition on inferring use of recalled
  context from its delivery.
- [ ] State the retention, redaction, coverage, local-only, and v1 exclusions.
- [ ] Add a short rollback statement: disabling collection stops new ingestion but
  does not delete existing sanitized activity.

**Failing check:** add `tests/adr/test_dec136_activity_boundary.py` that asserts the
decision file contains the memory separation, explicit-link, and no-timestamp-cause
rules before writing the ADR.

**Success metrics:**

- 1 accepted ADR exists and is discoverable through the established ADR index.
- The test fails when any of the three boundary rules is removed.
- No runtime code changes occur in this task.

## Task 1: Define the operational event contract and sanitization boundary

**Depends on:** Task 0

**Files:** create `src/axon/activity/__init__.py`, `models.py`, `sanitize.py`, and
`tests/activity/test_models.py`, `tests/activity/test_sanitize.py`.

**Subtasks:**

- [ ] Implement the versioned session, event, cursor, evidence-link, filter, and
  page models using Pydantic v2.
- [ ] Implement deterministic event IDs from native source identity, not arrival
  time, and validate UTC-aware timestamps.
- [ ] Redact credential-shaped values and named secret fields; replace excluded
  content with a marker and add the redaction reason.
- [ ] Reject environment-dump-shaped payloads, opaque encrypted reasoning, and
  restricted `work` payloads before they can be serialized.
- [ ] Preserve unknown-but-safe source fields under an explicit
  `unsupported-format` or `partial-observable` coverage marker rather than
  guessing their meaning.

**Failing checks:**

- `test_event_id_is_identical_for_the_same_native_record`
- `test_secret_fixture_never_survives_sanitization`
- `test_restricted_work_content_is_excluded_by_default`
- `test_unknown_format_is_visible_not_silently_dropped`

**Success metrics:**

- 100% of fixture secrets are absent from the sanitized serialized event.
- Re-sanitizing an event is byte-equivalent and retains the same `event_id`.
- Every excluded or unsupported fixture has a non-empty coverage/redaction marker.

## Task 2: Add PostgreSQL storage and a durable activity spool

**Depends on:** Task 1

**Files:** create `src/axon/store/migrations/pg/0006_activity_history.sql`,
`src/axon/activity/spool.py`, `src/axon/activity/repository.py`, and
`tests/activity/test_spool.py`, `tests/activity/test_repository.py`.

**Subtasks:**

- [ ] Add `activity_sessions`, `activity_events`, `activity_cursors`, and
  `activity_evidence_links`. Store event content as JSONB and enforce unique
  `(harness, source_id)` for idempotency.
- [ ] Index session timeline order, project/harness/date/outcome filters, and text
  search. Store source-cursor identity and offset/checkpoint with the transaction.
- [ ] Store a file source fingerprint plus byte offset in each file-backed cursor;
  detect replacement and truncation before resuming from an offset.
- [ ] Write sanitized payloads atomically to an activity-specific spool before the
  collector reports capture accepted. Reuse `pending.py`'s atomic rename and
  chronological drain approach, not a second queue framework.
- [ ] Replay each spool item through an upsert transaction that advances its cursor
  only with the corresponding persisted events.
- [ ] On database outage leave spool records pending and return a visible
  non-durable result. On malformed spool input quarantine it visibly.

**Failing checks:**

- `test_replaying_the_same_spool_item_creates_one_event`
- `test_cursor_does_not_advance_when_event_transaction_fails`
- `test_replaced_source_never_reuses_an_old_offset`
- `test_database_failure_keeps_sanitized_spool_item`
- `test_partial_or_corrupt_spool_item_is_recoverable_or_quarantined`

**Success metrics:**

- Two replays of 100 fixture events leave exactly 100 database events.
- Forced database failure leaves 100% of accepted fixture events in the spool.
- Crash between write and replay loses zero acknowledged records.
- No unsanitized source string appears in spool, database, or warning fixture.

## Task 3: Build ingestion, inspection, export, and recovery services

**Depends on:** Task 2

**Files:** create `src/axon/activity/service.py`, `src/axon/activity/export.py`,
`tests/activity/test_service.py`, and `tests/activity/test_export.py`.

**Subtasks:**

- [ ] Implement bulk ingestion that accepts normalized events and a source cursor,
  writes the spool first, then drains it idempotently.
- [ ] Implement session lookup, timeline lookup, text search, and filters for
  project, harness, date, and outcome.
- [ ] Emit portable versioned JSONL containing the sanitized event, source
  provenance, relationships, and coverage, not a path to a source log.
- [ ] Implement import into an empty database without changing stable event IDs.
- [ ] Return explicit collection health: pending count/bytes, stored bytes, latest
  error, and source compatibility warnings.

**Failing checks:**

- `test_export_import_preserves_ids_content_and_relationships`
- `test_deleted_source_log_does_not_affect_timeline_search_or_export`
- `test_search_does_not_cross_project_or_harness_filter`
- `test_health_reports_pending_spool_after_database_outage`

**Success metrics:**

- Export then import of a fixture session preserves 100% of event IDs and links.
- Removing the fixture source after import changes zero timeline/search/export rows.
- Filtered search returns 0 cross-filter rows in fixtures designed to collide.

## Task 4: Expose the minimal `axon activity` CLI

**Depends on:** Task 3

**Files:** modify `src/axon/cli/pb.py`; create `tests/cli/test_activity_commands.py`.

**Subtasks:**

- [ ] Add one Typer group named `activity` without refactoring existing CLI groups.
- [ ] Add `import --harness --path`, `collect`, `export --session`, `search`, and
  `show --session` commands backed only by Task 3 services.
- [ ] Make `import` an explicit historical action. `collect` performs live-source
  recovery and pending-spool draining only, with no scheduled LLM activity.
- [ ] Use machine-readable JSON output where existing AXON CLI conventions permit;
  otherwise print stable fields including coverage and warnings.
- [ ] Reject unsupported harness values and missing paths with a non-zero exit and
  actionable message, never a false success.

**Failing checks:**

- `test_activity_import_then_show_returns_sanitized_event`
- `test_activity_export_is_versioned_jsonl`
- `test_activity_collect_reports_pending_spool`
- `test_activity_import_unknown_harness_fails_loudly`

**Success metrics:**

- A fixture import, source deletion, `show`, `search`, and `export` all succeed.
- Every command reports coverage or compatibility state.
- Unsupported input exits non-zero and persists no partial cursor.

## Task 5: Add the Claude Code adapter

**Depends on:** Task 4

**Files:** create `src/axon/activity/adapters/base.py`, `claude_code.py`, fixture
JSONL under `tests/fixtures/activity/claude_code/`, and
`tests/activity/test_claude_code_adapter.py`; modify existing Claude hook code only
to trigger collection after the adapter is proven.

**Subtasks:**

- [ ] Incrementally parse the installed Claude session JSONL format, including
  linked subagent logs when an explicit parent identifier is present.
- [ ] Map messages, observable tool calls/results, usage records, and parent links
  to the common model. Do not infer missing tool-result association.
- [ ] Persist and reconcile a cursor across append, partial line, replacement, and
  truncation. A hook accelerates collection but filesystem discovery/recovery is
  the source of truth.
- [ ] Surface unsupported records through compatibility warnings rather than
  dropping the whole session.

**Failing checks:**

- `test_claude_subagent_events_keep_explicit_parent_session`
- `test_partial_line_is_ingested_after_completion_once`
- `test_log_replacement_reconciles_without_duplicates`
- `test_tool_result_links_only_by_native_call_id`

**Success metrics:**

- Controlled fixture with a parent and subagent produces two sessions and correct
  parent link.
- Append/restart/reimport produces no duplicates and no skipped completed line.
- Every unparseable fixture record becomes one visible compatibility warning.

## Task 6: Add Codex adapters for interactive and `exec --json`

**Depends on:** Task 4

**Files:** create `src/axon/activity/adapters/codex.py`, fixtures under
`tests/fixtures/activity/codex/`, and `tests/activity/test_codex_adapter.py`.

**Subtasks:**

- [ ] Parse installed interactive rollout files incrementally using native session,
  turn, and call identifiers.
- [ ] Parse `codex exec --json` records and a separately captured submitted prompt;
  reconcile only through native identifiers. Persist a submitted prompt only when
  the explicit execution wrapper or source record supplied it; never reconstruct
  it from a command line, working directory, or later model output.
- [ ] Keep unknown rollout versions readable as a session with
  `unsupported-format` coverage and a visible warning.
- [ ] Store reported usage as raw observations. Do not estimate cost, sum
  cumulative snapshots, or represent subscription usage as zero cost.

**Failing checks:**

- `test_codex_exec_prompt_and_json_events_reconcile_by_native_id`
- `test_codex_exec_without_observable_prompt_is_marked_partial`
- `test_repeated_cumulative_usage_snapshot_does_not_inflate_total`
- `test_unknown_rollout_format_is_visible`
- `test_concurrent_same_repo_codex_sessions_remain_separate`

**Success metrics:**

- Two concurrent fixture sessions in the same repository have 0 cross-session
  events.
- Repeated cumulative usage leaves the displayed total equal to one snapshot.
- Unsupported installed-format fixture remains searchable as a warning session.

## Task 7: Add captured AGY terminal execution with honest coverage

**Depends on:** Task 4

**Files:** create `src/axon/activity/adapters/agy.py`,
`src/axon/activity/agy_runner.py`, fixtures under `tests/fixtures/activity/agy/`,
and `tests/activity/test_agy_adapter.py`; modify `src/axon/cli/pb.py` to add
`axon activity run --harness agy -- ...`.

**Subtasks:**

- [ ] Reuse the existing AGY launcher transport invariants: PTY, `--new-project`,
  final prompt argument, process group, timeout kill, and stdout/stderr capture.
  Keep the implementation AXON-owned so `agy-plugin-cc` is not a runtime dependency.
- [ ] Capture submitted prompt, terminal output, process exit, timeout, and observed
  workspace change evidence. Mark internal model actions and usage unavailable.
- [ ] Treat process exit 0 as `process-succeeded`, not verified task success.
- [ ] Require an explicit AGY invocation for capture. Do not observe arbitrary
  terminal sessions or claim their internal activity was captured.

**Failing checks:**

- `test_agy_timeout_and_successful_exit_have_distinct_outcomes`
- `test_agy_zero_exit_is_not_verified_task_success`
- `test_agy_coverage_marks_internal_actions_unavailable`
- `test_workspace_diff_does_not_attribute_concurrent_unrelated_edit`

**Success metrics:**

- Timeout fixture produces `timed_out`; exit-zero fixture produces
  `process_succeeded`; neither is labeled verified success.
- 100% of AGY sessions state partial coverage and unavailable usage.
- Concurrent workspace fixture has 0 falsely attributed file changes.

## Task 8: Link existing AXON memory and documents by explicit provenance

**Depends on:** Tasks 2 and 4

**Files:** modify only the existing capture/recall models and their PostgreSQL
repositories after tracing their writers; create `tests/activity/test_provenance.py`
and the narrowest impacted existing tests.

**Subtasks:**

- [ ] Add nullable operational session, turn, and event references to the relevant
  capture/recall records through `activity_evidence_links`, with indexes and
  referential validation. A link has `target_type`, `target_id`, `session_id`,
  optional `turn_id`/`event_id`, and an explicit `relation` of `context-delivered`
  or `record-supported`; no free-form inferred relation exists.
- [ ] Record which AXON context was returned, as delivery evidence only.
- [ ] Link decisions/documents only when an explicit activity identifier arrives
  from the caller or adapter. Do not construct historical links from timestamps,
  repositories, or text similarity.
- [ ] Keep current capture, promotion, and recall behavior unchanged when no
  provenance reference is supplied.

**Failing checks:**

- `test_explicit_activity_reference_resolves_from_memory_record`
- `test_recalled_context_is_recorded_as_delivered_not_used`
- `test_existing_memory_without_provenance_is_still_valid`
- `test_shared_workspace_and_timestamp_do_not_create_provenance_link`

**Success metrics:**

- Explicit fixture IDs resolve end-to-end; inferred-correlation fixture creates 0 links.
- Existing memory and recall test suite remains unchanged and green.
- No provenance migration requires backfilling unsupported historical links.

## Task 9: Add authenticated activity APIs

**Depends on:** Tasks 3 and 8

**Files:** modify `src/axon/http/app.py`; create `tests/http/test_activity_api.py`.

**Subtasks:**

- [ ] Add paginated `GET /api/activity/sessions`, session timeline, search, and
  collection-health endpoints alongside the existing dashboard APIs.
- [ ] Reuse the current auth dependency, loopback bind assumptions, cache headers,
  and response conventions.
- [ ] Validate pagination limits, opaque cursor handling, and filters. Return
  unavailable metrics as `null` plus coverage, never zero or an estimate.
- [ ] Return only sanitized stored text and safe structured metadata.

**Failing checks:**

- `test_activity_routes_require_existing_dashboard_auth`
- `test_sessions_api_paginates_without_duplicate_or_missing_row`
- `test_unavailable_usage_is_null_not_zero`
- `test_api_never_returns_secret_fixture`

**Success metrics:**

- Unauthorized requests to all new routes return the existing auth failure.
- Paginating a 101-row fixture returns each row exactly once.
- Secret fixture is absent from every JSON response.

## Task 10: Extend the dashboard and complete controlled verification

**Depends on:** Tasks 5-9

**Files:** modify `src/axon/http/dashboard.py`; create
`tests/http/test_activity_dashboard.py`, `tests/http/test_activity_dashboard_xss.py`,
and `tests/integration/test_activity_history_restore.py`.

**Subtasks:**

- [ ] Add Sessions, Session Timeline, and Operational Summary views to the existing
  self-contained dashboard. Keep the existing dashboard content working.
- [ ] Add text search and project, harness, date, and outcome filters.
- [ ] Display latest activity, status, coverage, observed failures, duration,
  reported tokens, available cost, ingestion errors, pending spool bytes, and stored
  data bytes. Label unavailable values exactly as unavailable.
- [ ] Construct all captured text with `textContent`/DOM nodes and add hostile HTML
  fixtures for prompt, tool output, and warning text.
- [ ] Run one controlled session per harness, compare observable source records to
  the stored timeline, then test PostgreSQL backup and restoration before declaring
  durability.

**Failing checks:**

- `test_activity_dashboard_renders_hostile_text_inertly`
- `test_dashboard_labels_unavailable_cost_without_zero`
- `test_controlled_harness_fixture_matches_timeline_observations`
- `test_backup_restore_preserves_activity_export_identity`

**Success metrics:**

- Hostile HTML creates 0 executable nodes, event-handler attributes, or HTML sinks.
- Claude, Codex, and AGY controlled fixtures each match all observable records and
  show documented gaps for unavailable data.
- Backup, restore, and export/import preserve 100% of fixture event IDs,
  relationships, sanitized content, and ordering.

## Release gates and measurable acceptance

The release is accepted only when all metrics below hold in an isolated PostgreSQL
test environment, then the controlled local runs complete.

| Requirement | Evidence | Required result |
| --- | --- | --- |
| Concurrent agents remain separate | two same-repo concurrent fixtures | 0 cross-session events |
| Call/result and delegation correlation | native-ID fixtures | 100% correct native-ID links; 0 timestamp-only links |
| Replay and crash recovery | duplicate/restart fixture | 0 duplicate IDs; 0 lost acknowledged records |
| Partial/rotated/unknown sources | source mutation fixtures | recovered once or one visible warning per unsupported record |
| Source independence | import then delete source | timeline, search, and export still return stored events |
| Database outage honesty | forced asyncpg failure | spool retained, health degraded, no false durable success |
| Sanitization and XSS | secret and hostile HTML fixtures | 0 secrets persisted; 0 executable DOM injections |
| Usage correctness | cumulative and subscription fixtures | no inflated total; unavailable cost never shown as zero |
| AGY semantics | timeout and exit-zero fixtures | distinct outcomes; no verified-success claim from exit code |
| File-change evidence | concurrent workspace fixture | observed diff shown; 0 false attribution |
| Provenance | explicit and ambiguous fixtures | explicit links resolve; ambiguous links remain absent |
| Durability | backup/restore plus empty-DB import | 100% IDs/content/relationships preserved |

## Non-goals and deliberate omissions

- No automatic deletion or retention scheduler. Data remains until an explicit
  deletion feature is separately designed.
- No effort to reconstruct missing historical content, hidden model actions, or
  unrecorded file contents.
- No claim that AXON-delivered context was internally used by a model.
- No external observability export, multi-user access, multi-host coordinator, or
  framework migration. Add one only when personal local use no longer fits.

## Final host verification

Run after Task 10, from the accepted AXON checkout:

```bash
rtk pytest tests/activity tests/cli/test_activity_commands.py tests/http/test_activity_api.py tests/http/test_activity_dashboard.py tests/http/test_activity_dashboard_xss.py tests/integration/test_activity_history_restore.py -q
rtk ruff check src/axon/activity src/axon/cli/pb.py src/axon/http tests/activity tests/http
rtk python3 -m compileall src/axon/activity src/axon/http
```

Then run the three controlled harness sessions, export each, restore into an empty
test database, and compare IDs, ordering, relationships, and sanitized content.
The host records the commands and outputs in the final Task 10 commit message or
its accompanying PR description.
