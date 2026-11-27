# F0.2 spool ignore checks (AXON #202)

Build: `ruff check src`; `python3 -m compileall -q src`
Run spool: `python3 -m pytest tests/store/test_spool_refuses_unignored.py tests/store/test_spool_refusal_record.py tests/cli/test_spool_not_committable.py tests/cli/test_spool_ignore_edges.py tests/cli/test_spool_recover_ignore.py tests/cli/test_spool_drain_refuses.py tests/cli/test_hook_spool.py tests/cli/test_status_spool_refusals.py tests/activity/test_activity_spool_refuses.py tests/doctor/test_check_spool_refusals.py tests/store/test_pending.py -v`
Plan: `.specs/features/f0-2-spool-ignore/plan.md`

Test command for the whole repo (plan Gate): `ruff check src && python3 -m compileall -q src && python3 -m pytest -q`

## Intent

22 checks in 4 slices · 2 one-way doors · 0 open

Checks C1 to C18 are the plan's criteria 1-18, same numbers. C19-C22 are added obligations: C20-C22 are
Vera's builder notes (critic-vera.md, r2 delta; note 1 is part of C6 and C13), C19 closes a
fail-open found while reading the code. No criterion is changed. Build order: C2, C12, C11, C4,
C5, C6, C19, C13, C14, C15, C16, C3, C7, C20, C8, C9, C10, C17, C21, C22, C18, C1.
Each check is seen red before the code that turns it green; the "Red" line says how.

## Checks

### S1 - no stageable payload is ever written

**C1** - With a successful ignore write the payload is written and `git add -A` stages nothing under `pending/` (AC 1)
Proof: run spool | test_git_add_all_stages_nothing_under_pending_after_the_hook_spooled PASSED
Red: guard, passes before the change; deliberate break: delete the rule write, see it fail, revert.

**C2** - `os.replace` raising `OSError(30)` with the spool not ignored: `SpoolNotIgnoredError` containing `OSError: [Errno 30] Read-only file system`, 0 `*.json`, no `.gitignore.*.tmp` (AC 2)
Proof: run spool | test_write_pending_refuses_when_ignore_write_fails PASSED
Red: at f898c82d the error is swallowed and the payload is written.

**C3** - `.axon/.gitignore` is a directory: the session hook leaves 0 `*.json` under `pending/` and `git add -A` stages nothing there (AC 3)
Proof: run spool | test_ignore_file_write_oserror_refuses_the_session_hook_spool PASSED
Red: at f898c82d 1 payload is written. Replaces `test_ignore_file_write_oserror_is_swallowed_during_session_hook_spool` in the same commit.

**C4** - A tracked file under `pending/`: `write_pending` raises, message contains `tracked`, no new file, for absolute and unset `AXON_DATA_ROOT` (AC 4)
Proof: run spool | test_write_pending_refuses_when_a_spool_file_is_tracked[absolute] PASSED
Proof: run spool | test_write_pending_refuses_when_a_spool_file_is_tracked[relative] PASSED
Red: before the git proof the second file is written.

**C5** - Negated rules (`!pending/`, `!pending-quarantine/`): `quarantine_invalid` raises and the source stays in `pending/`, absolute and unset (AC 5)
Proof: run spool | test_quarantine_refuses_when_check_ignore_fails[absolute] PASSED
Proof: run spool | test_quarantine_refuses_when_check_ignore_fails[relative] PASSED
Red: at f898c82d the file is moved.

**C6** - `rev-parse` saying `false` or `not a git repository`: `write_pending` writes 1 file and raises nothing (AC 6; Vera note 1: the final-name check returns the same way)
Proof: run spool | test_write_pending_outside_a_work_tree_writes[tmp_dir] PASSED
Proof: run spool | test_write_pending_outside_a_work_tree_writes[under_dot_git] PASSED
Red: guard, passes before the change; deliberate break: treat "outside a work tree" as a failure, see it fail, revert.

**C11** - `subprocess.run` raising `FileNotFoundError`: `_ensure_spool_ignored` raises, message contains `git not found`, nothing written (AC 11)
Proof: run spool | test_refuses_when_git_is_missing PASSED
Red: no git call exists, the payload is written.

**C12** - Any other `rev-parse` result: `.axon` as a regular file is refused with the kept write error; `TimeoutExpired` gives `git timed out after 10s`; nothing written (AC 12)
Proof: run spool | test_unclassified_rev_parse_result_refuses[axon_is_a_file] PASSED
Proof: run spool | test_unclassified_rev_parse_result_refuses[git_timeout] PASSED
Red: no `SpoolNotIgnoredError` exists before the change.

**C13** - `pending/*` plus `!pending/sess-*.json`: `write_pending(commit_hash="sess")` raises and `git status` lists nothing under `.axon/pending/` (AC 13)
Proof: run spool | test_partial_name_negation_is_refused PASSED
Red: the payload is written and shows as untracked.

**C14** - Failed rule write while `.axon/` is already ignored: 1 payload and one stderr line starting `[axon] spool ignore rule not written (OSError:` (AC 14)
Proof: run spool | test_write_error_with_proven_ignore_writes_and_reports PASSED
Red: nothing is printed.

**C19** - With `GIT_DIR=.git` exported (as inside a git hook), a tracked spool file is still refused: the proof does not inherit repo-location variables (added; AC 4, AC 6)
Proof: run spool | test_inherited_git_dir_does_not_fail_open PASSED
Red: with the plain inherited environment `rev-parse` says `not a git repository: '.git'`, which reads as outside, and the payload is written.

### S2 - the refusal is loud

**C7** - `axon pending recover` without proof exits 1, prints a stderr line starting `spool not ignored:` containing `OSError`, moves nothing (AC 7)
Proof: run spool | test_recover_refuses_and_exits_1_when_ignore_cannot_be_proven PASSED
Red: a traceback or a move instead of the line and exit 1.

**C20** - `pending recover` checks every target before moving any: a refused second target leaves both files in `pending-quarantine/` (added; Vera note 3)
Proof: run spool | test_recover_checks_every_target_before_moving_any PASSED
Red: the first file is moved.

**C8** - `axon pending drain` that would quarantine without proof exits 1 with `spool not ignored:` and the malformed file stays (AC 8)
Proof: run spool | test_drain_exits_1_when_quarantine_is_refused PASSED
Red: a raw traceback, no `spool not ignored:` line on stderr.

**C9** - Store down and no proof: `session-hook` and `compact-hook` exit 0, print `spool not ignored:` on one stderr line, write 0 payloads (AC 9)
Proof: run spool | test_hook_reports_refused_spool_and_exits_0[session-hook] PASSED
Proof: run spool | test_hook_reports_refused_spool_and_exits_0[compact-hook] PASSED
Red: at f898c82d 1 payload is written and no such line.

**C10** - `ActivityService.ingest_events` without proof raises `SpoolNotIgnoredError`, 0 `*.json` under `activity-pending/` (AC 10)
Proof: run spool | test_ingest_refuses_unignored_spool PASSED
Red: at f898c82d the event is written. Deliberate break after C2: swallow the error in `ingest_events`, revert.

### S3 - a dropped payload is visible

**C15** - Each refusal updates `spool-refusals.json` to `{count, last_at, last_dir, last_reason}`; two refusals give `count` 2 (AC 15)
Proof: run spool | test_each_refusal_updates_the_record PASSED
Red: no record file exists.

**C16** - Record path is a directory: still `SpoolNotIgnoredError`, hooks exit 0, one stderr line starting `[axon] could not record spool refusal:` (AC 16)
Proof: run spool | test_unwritable_record_does_not_change_the_refusal PASSED
Red: no record and no line.

**C17** - `axon doctor` shows `[warn] capture.spool_refusals` with count, time, reason and the delete suggestion; `[ ok ]` `no spool refusals` without a record (AC 17)
Proof: run spool | test_doctor_warns_on_recorded_refusals PASSED
Proof: run spool | test_doctor_ok_without_record PASSED
Red: no such check exists.

**C18** - `axon status` prints `spool refusals: <count> (last <last_at>: <last_reason>)` first, before the store is queried; `spool refusals: 0` without a record (AC 18)
Proof: run spool | test_status_first_line_reports_refusals PASSED
Proof: run spool | test_status_reports_zero_without_record PASSED
Red: the first line is `repo:`.

### S4 - the record never breaks its readers

**C21** - `run_all_checks(data_root=X)` reads `X/spool-refusals.json`; a corrupt record makes doctor warn `unreadable`, status say `unreadable`, and the next refusal rewrite `count` 1 (added; Vera note 2)
Proof: run spool | test_doctor_reads_the_given_data_root PASSED
Proof: run spool | test_corrupt_record_is_reported_and_rewritten PASSED
Red: `run_all_checks(data_root=X)` or a corrupt record raises or reads the wrong file.

**C22** - A failed `os.replace` while recording leaves no `spool-refusals.*.tmp` (added; Vera note 3)
Proof: run spool | test_failed_record_replace_leaves_no_temp_file PASSED
Red: deliberate break: remove the unlink, see the temp file, revert.

## Coverage

| Set (size) | Member -> proof | Unproven |
| --- | --- | --- |
| payload writers and movers (3) | `write_pending` C2 · `quarantine_invalid` C5 · `pending_recover` C7 | - |
| callers of the spool (4) | session-hook C9 · compact-hook C9 · activity ingest C10 · drain C8 | - |
| rev-parse results (4) | `true` C4 · `false` C6 · `not a git repository` C6 · anything else C12 | - |
| failed proofs (5) | tracked C4 · negated directory C5 · negated file name C13 · git missing C11 · git timeout C12 | - |
| data root forms (2) | absolute C4 · relative C4 | - |
| door 1 `SpoolNotIgnoredError` (1) | C2 | - |
| door 2 `spool-refusals.json` (1) | C15 | - |
| record readers (3) | doctor C17 · status C18 · next refusal C21 | - |
| record states (4) | absent C17 · present C18 · corrupt C21 · unwritable C16 | - |

## Swept

- validation: C4, C5, C13
- failure modes: C2, C16, C22
- idempotency: C15
- authorization: n/a - local CLI acting on the user's own files; no caller identity is involved
- concurrency: n/a - the record is best effort with an atomic replace; two refusals at once may count one, and a refused payload is never written either way
- data lifecycle: C15 (the record is never cleared automatically, plan Out of scope)
- dependency failure: C11, C12
- state transitions: C7, C20
- observability: C14, C15, C17, C18

## Handoff

- **Settled mid-build:** Q1 answered before the build, Sammy: "Seguir com aviso (Recommended)".
- **Dispatched:** none - the independent review is Rita's and Vera's, dispatched by Otto.
- **Boundary:** C1-C22 closed at the commit before this one
- **Red at f898c82d:** the new test files run against the original source (plus a stub `SpoolNotIgnoredError` class that nothing raises, so the files import): 29 failed, 14 passed. The passing ones are existing tests and the C6 guard.
- **Deliberate breaks (seen red, then reverted):** C1 rule write replaced by deleting the temp file; C5 `check-ignore` on the directory skipped; C6 `false` and `not a git repository` each treated as a failure; C10 `ingest_events` swallowing the spool error.
- **Abandoned:** none
- **Host:** Windows, no Docker; Postgres-backed tests and the Linux runs are CI's.
