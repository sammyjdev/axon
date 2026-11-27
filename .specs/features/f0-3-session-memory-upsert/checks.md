# F0.3 #187 session memory upsert checks

Build: `ruff check src`; `python3 -m compileall -q src`
Run pg: `python3 -m pytest tests/store/test_pg_session_memory_upsert.py tests/store/test_pg_session_hooks_e2e.py tests/store/test_pg_migrations.py tests/store/test_session_duplicates.py tests/store/test_pg_session_repository.py -v`
Run local: `python3 -m pytest tests/store/test_session_id_normalize.py tests/store/test_session_memory_pending.py tests/cli/test_session_hook_session_id.py tests/cli/test_session_duplicates_cli.py tests/mcp/test_get_session_memory_text.py -v`
Run hooks: `python3 -m pytest tests/hooks/test_session_end.py tests/cli/test_agent_hooks.py tests/cli/test_session_memory_repo_key.py tests/cli/test_session_memory_repo_key_gaps.py -v`
Plan: `.specs/features/f0-3-session-memory-upsert/plan.md`

30 checks in 4 slices · 3 one-way doors · 0 open, of which 0 block

Check ids follow the plan's criterion numbers. Criteria 12-14 apply only under Q2-B; Sammy chose Q2-A, so there is no C12, C13 or C14. C29-C33 come from the plan's Builder notes and Assumptions.

The `pg` group needs Docker (testcontainers). This build host has none and no other database may be used, so the `pg` group is seen red and green only in the CI full-suite job.

## Checks

### S1 - one row per session

**C1** - Two saves of `s-1` (T1, then T2 > T1) leave 1 row with the T2 summary and return the same id (AC 1)
Proof: run pg | test_same_session_id_upserts_one_row PASSED
Red: tests-only commit in CI (no `session_id` column).

**C2** - An older digest for `s-1` leaves the row byte-identical and returns the existing id (AC 2)
Proof: run pg | test_older_snapshot_does_not_overwrite_and_returns_the_id PASSED
Red: tests-only commit in CI.

**C3** - 0008 keeps every old row identical, adds five NULL per-kind columns, builds both partial indexes, and a second pass records nothing (AC 3)
Proof: run pg | test_0008_adds_session_id_without_touching_rows PASSED
Red: tests-only commit in CI (no 0008 file).

**C4** - With no `session_id`, 2 different saves give 2 rows and an identical save returns the same id (AC 4)
Proof: run pg | test_rows_without_session_id_keep_natural_key PASSED
Proof: run pg | test_large_summary_and_note_roundtrip_and_dedup PASSED
Red: guard, passes at base. Break: upsert `None` on one key.

**C5** - `session-hook` run twice with `session_id` `abc` leaves 1 row keyed `abc` (AC 5)
Proof: run pg | test_session_hook_twice_keeps_one_row PASSED
Proof: run local | test_hook_passes_session_id_to_the_row PASSED
Red: run before wiring the hook; the field is absent.

**C6** - `compact-hook` with `session_id` `abc` saves a `SessionMemory` carrying `abc` (AC 6)
Proof: run local | test_compact_hook_passes_session_id PASSED
Red: run before wiring the hook.

**C15** - A 2,000-byte compact summary at T1 stays visible after a 60-byte digest at T2, both through the hooks (AC 15)
Proof: run pg | test_thin_digest_after_compact_does_not_overwrite PASSED
Red: tests-only commit in CI.

**C16** - A later digest with equal or more bytes than the compact summary becomes visible (AC 16)
Proof: run pg | test_digest_as_rich_as_compact_replaces_it PASSED
Red: tests-only commit in CI.

**C17** - Equal-time digests give the same row in both orders: more bytes, else higher text in byte order, else more raw_turns; an identical snapshot changes nothing (AC 17)
Proof: run pg | test_equal_time_is_arrival_independent PASSED
Red: tests-only commit in CI.

**C18** - With no compact summary the newer digest wins even when thinner; a compact summary newer than every digest wins even when thinner (AC 18)
Proof: run pg | test_newer_digest_or_compact_replaces PASSED
Red: tests-only commit in CI.

**C19** - A save for `s-1` from another project changes nothing and returns the id (AC 19)
Proof: run pg | test_other_project_does_not_take_over_a_session PASSED
Red: tests-only commit in CI.

**C20** - `"  abc "` is stored as `abc`; `""`, `"   "`, `123`, `{}`, absent and 201 characters are `None` and nothing raises (AC 20)
Proof: run local | test_normalize_session_id PASSED
Proof: run local | test_malformed_session_id_saves_without_key PASSED
Red: run before `normalize_session_id` exists.

**C21** - Two session ids with identical project, summary, raw_turns and created_at give 2 rows (AC 21)
Proof: run pg | test_identical_content_in_two_sessions_keeps_two_rows PASSED
Red: tests-only commit in CI.

**C22** - Concurrent saves of one id, 20 times, digest pair and compact/digest pair: 1 row, no error, same id, row equals the sequential result (AC 22)
Proof: run pg | test_concurrent_upserts_keep_one_row PASSED
Red: tests-only commit in CI.

**C23** - Two initializers record 0008 once; with `ACCESS SHARE` held the runner raises `LockNotAvailableError` within 6 s and records nothing (AC 23)
Proof: run pg | test_concurrent_initializers_apply_0008_once PASSED
Proof: run pg | test_blocked_migration_times_out_and_records_nothing PASSED
Red: tests-only commit in CI.

**C28** - With advisory lock 7101870187 held the runner raises `LockNotAvailableError` within 6 s, and applies 0008 once after release (AC 28)
Proof: run pg | test_held_advisory_lock_times_out PASSED
Red: tests-only commit in CI.

**C24** - Reads carry `session_id`, order by `created_at DESC, id DESC`, and the MCP text equals the base text (AC 24)
Proof: run pg | test_readback_carries_session_id_and_stable_order PASSED
Proof: run local | test_output_text_unchanged PASSED
Red: readback in the tests-only CI commit; the golden text is a guard recorded at base, break: change the line format.

**C25** - The four existing hook test files pass with `git diff c05edf5 -- <file>` empty for each (AC 25)
Proof: run hooks | test_session_end_runs_the_import_after_the_digest_and_a_failing_step_stops_nothing PASSED
Proof: `git diff --exit-code c05edf5 -- tests/hooks/test_session_end.py tests/cli/test_agent_hooks.py tests/cli/test_session_memory_repo_key.py tests/cli/test_session_memory_repo_key_gaps.py`
Red: guard. Break: add a third argument to the `STEPS` call.

**C26** - The compact summary replayed from the spool after the digest gives the same row as the forward order, for both pairs (AC 26)
Proof: run pg | test_reverse_replay_gives_the_same_row PASSED
Red: tests-only commit in CI.

**C27** - A compact summary and a digest with equal time and text show the compact summary in both orders, and a later 40-byte digest changes nothing visible (AC 27)
Proof: run pg | test_equal_text_cross_kind_tie_is_deterministic PASSED
Red: tests-only commit in CI.

**C30** - `SessionMemory` itself turns `""` and `"   "` into `None`, so a direct caller cannot index an empty string (Builder note)
Proof: run local | test_session_memory_normalizes_blank_session_id PASSED
Red: run before the validator exists.

**C31** - After a hook returns or fails, `current_hook_session_id` is `None` again and the next manual save has no id (Builder note)
Proof: run local | test_hook_session_id_does_not_leak_to_the_next_save PASSED
Red: run before the hook sets the value; break: remove the reset.

### S2 - the spool keeps the key

**C7** - A spooled save carries `session_id` and the snapshot's `created_at`; a late drain keeps the newer row, returns its id and unlinks the file; a missing or non-string key drains as `None` (AC 7)
Proof: run pg | test_late_drain_after_newer_save PASSED
Proof: run local | test_legacy_payload_without_session_id_drains PASSED
Proof: run local | test_malformed_spooled_session_id_drains_without_key PASSED
Red: late drain in the tests-only CI commit; local ones before the drain passes the key.

### S3 - legacy duplicates are reported

**C8** - The report prints one line per exact-text group and the count line, exits 0 and changes no row (AC 8)
Proof: run pg | test_report_changes_nothing PASSED
Proof: run local | test_report_through_axon_app PASSED
Red: before the command exists (`No such command`, exit 2).

**C9** - 30 legacy rows keep the same checksum and a NULL `session_id` through migration, 3 hook saves and a report (AC 9)
Proof: run pg | test_no_legacy_row_is_changed PASSED
Red: tests-only commit in CI.

**C10** - 5 old legacy rows and 3 newer keyed sessions: the top 3 are the keyed ones (AC 10)
Proof: run pg | test_keyed_sessions_lead_recall PASSED
Red: tests-only commit in CI.

**C11** - Store unreachable exits 1 with stderr `cannot reach the session store:`; an unknown option exits 2 (AC 11)
Proof: run local | test_store_down_exits_1 PASSED
Proof: run local | test_unknown_option_exits_2 PASSED
Red: before the command exists.

### S4 - assumptions the plan asks the builder to record

**C29** - 0008 on 1,062 legacy rows finishes under 5 s and the test warns with the measured time, so the CI log shows it (Assumption 4)
Proof: run pg | test_0008_on_1062_rows_is_short PASSED
Red: tests-only commit in CI.

**C32** - Report mode only: there is no `--apply` or `--restore` option and no backup table (Q2-A)
Proof: run local | test_unknown_option_exits_2 PASSED
Proof: run pg | test_report_changes_nothing PASSED
Red: before the command exists.

**C33** - A manual `axon session save` stores `session_id = None` and the command has no `--session-id` option (plan Shape 2)
Proof: run local | test_manual_save_has_no_session_id PASSED
Red: before the field exists.

## Coverage

| Set (size) | Member -> proof | Unproven |
| --- | --- | --- |
| acceptance criteria under Q2-A (25) | `1` C1 · `2` C2 · `3` C3 · `4` C4 · `5` C5 · `6` C6 · `7` C7 · `8` C8 · `9` C9 · `10` C10 · `11` C11 · `15` C15 · `16` C16 · `17` C17 · `18` C18 · `19` C19 · `20` C20 · `21` C21 · `22` C22 · `23` C23 · `24` C24 · `25` C25 · `26` C26 · `27` C27 · `28` C28 | - |
| one-way doors (3) | `upsert and pick` C1, C26 · `natural key for NULL only` C4, C21 · `serialized bounded migration` C23, C28 | - |
| `session_id` input values (7) | `"  abc "` C20 · `""` C20 · `"   "` C20 · `123` C20 · `{}` C20 · `absent` C20 · `201 chars` C20 | - |
| report command exits (3) | `0` C8 · `1` C11 · `2` C11 | - |
| report group rule, AC 8 (3 examples) | `different raw_turns` C8 · `trailing space` C8 · `keyed row` C8 | - |
| write entry points (4) | `session-hook` C5 · `compact-hook` C6 · `manual save` C33 · `spool drain` C7 | - |
| equal-time tie keys, AC 17 (4) | `bytes` C17 · `text order` C17 · `raw_turns` C17 · `identical` C17 | - |

## Swept

- validation: C20, C30
- failure modes: C7, C23, C28
- idempotency: C2, C4, C17
- authorization: C19
- concurrency: C22, C23
- data lifecycle: C3, C9
- dependency failure: C7, C11
- state transitions: C15, C16, C18, C26, C27
- observability: n/a - no new logging; the existing spool and hook messages on stderr are unchanged

## Hook payloads (plan Assumption 1)

Not captured live: a real SessionEnd and PostCompact payload needs a real provider session, which this build is not approved to run. Instead, the official page https://code.claude.com/docs/en/hooks (read 2026-11-27, as extracted by a fetch tool, not verbatim) lists `session_id` among the common input fields ("Current session identifier.") and shows `"session_id": "abc123"` in both the SessionEnd and the PostCompact examples. A payload without it takes the natural-key path (C4, C20).

## Handoff

- **Settled mid-build:** none
- **Dispatched:** none
- **Boundary:** none yet
- **Abandoned:** none
