# F0.1 hooks install in pre-commit repos - checks

Build: `python -m ruff check src`; `python -m compileall -q src`
Run feature: `python -m pytest tests/hooks/test_hooks_install_precommit.py tests/hooks/test_precommit_integration.py tests/hooks/test_hooks_cli.py tests/doctor/test_checks_capture_path_precommit.py tests/doctor/test_checks_capture_path_registry.py -v -rs -p no:cacheprovider`
Plan: `.specs/features/f0-1-hooks-precommit/plan.md`
Base: `f898c82d9746f28c309247416c98f771a201dbc2`
Gate: `ruff check src && python3 -m pytest -q` (CI on the pull request head)

18 checks in 3 slices · 2 one-way doors · 0 open, of which 0 block

Test files: `tests/hooks/test_hooks_install_precommit.py` (new), `tests/hooks/test_precommit_integration.py`,
`tests/hooks/test_hooks_cli.py`, `tests/doctor/test_checks_capture_path_precommit.py` (new). "Fail first" is the
test run before its code; every test is also run against the `f898c82d` sources, except where a check says guard.

## Checks

### S1 - success only when capture can run

**C1** - `--apply` on `repos: []` exits 0, writes the Appendix A bytes, passes `pre-commit validate-config`, leaves both drivers and one registry entry (AC 1)
Proof: run feature | test_apply_installs_drivers_and_registers_repo PASSED
Proof: run feature | test_apply_in_tty_mutates_pre_commit_config PASSED
Fail first: at f898c82d the config is invalid YAML and no driver or registry entry exists.

**C2** - A second run with entries present prints `[axon] AXON entries already in .pre-commit-config.yaml`, keeps the config bytes, installs both drivers, registers, exits 0, never `nada a fazer` (AC 2)
Proof: run feature | test_rerun_with_entries_present_installs_missing_drivers PASSED
Fail first: at f898c82d it prints `nada a fazer` and installs nothing.

**C3** - Only `.yml`: the driver holds `--config=.pre-commit-config.yml` and no repo path; both files: `.yaml` is used (AC 3)
Proof: run feature | test_yml_config_is_passed_by_name PASSED
Proof: run feature | test_yaml_wins_when_both_exist PASSED
Fail first: at f898c82d no driver exists.

**C4** - An AXON marker block in a hook or its `.legacy` file is gone from all four files after the run, and an emptied `.legacy` file is deleted (AC 4)
Proof: run feature | test_legacy_axon_block_is_stripped_before_install[post-commit] PASSED
Proof: run feature | test_legacy_axon_block_is_stripped_before_install[post-commit.legacy] PASSED
Fail first: at f898c82d the marker stays.

**C14** - With a trailing top-level key, the AXON item goes after `    hooks: []` and before `exclude:`; the result loads with 2 `repos` items and `exclude` = `^vendor/` (AC 14)
Proof: run feature | test_inserts_before_a_trailing_top_level_key PASSED
Fail first: at f898c82d `yaml.safe_load` raises on the result.

**C18** - With interpreter `/opt/my env/bin/python`, `entry` is a YAML double-quoted scalar and `shlex.split` of its value starts with that path (AC 18)
Proof: run feature | test_entry_survives_a_space_in_the_interpreter_path PASSED
Fail first: at f898c82d the first token is `/opt/my`.

### S2 - failure is loud and leaves nothing half-done

**C5** - Without `pre-commit` on `PATH`: exit 1, stderr contains `pre-commit not found`, config bytes unchanged (AC 5)
Proof: run feature | test_missing_binary_exits_1_before_writing PASSED
Fail first: at f898c82d it exits 0 and modifies the config.

**C6** - `pre-commit install` exiting 1: exit 1, stderr has `pre-commit install failed`, the tool's stderr and a `[axon] restored:` line; config, four hook files and registry are as before (AC 6)
Proof: run feature | test_pre_commit_install_failure_restores_and_exits_1 PASSED
Fail first: at f898c82d it exits 0 and modifies the config.

**C7** - Registry that is a JSON object or a directory: exit 1, stderr names `onboarded_repos.json`, registry unchanged, config and hooks restored, no driver left (AC 7)
Proof: run feature | test_registry_write_failure_restores_and_exits_1[json-object] PASSED
Proof: run feature | test_registry_write_failure_restores_and_exits_1[directory] PASSED
Fail first: at f898c82d it exits 0 and modifies the config.

**C8** - A zero exit that writes no driver: exit 1, stderr has `post-merge driver missing`, repo not registered, snapshot restored (AC 8)
Proof: run feature | test_verify_fails_when_stub_writes_no_driver PASSED
Fail first: at f898c82d it exits 0.

**C16** - On POSIX a driver written with mode 0644: exit 1, stderr has `post-commit driver not executable`, snapshot restored (AC 16)
Proof: run feature | test_non_executable_driver_fails
Fail first: at f898c82d it exits 0. Skipped only on `os.name == "nt"`, so it is proven by CI on ubuntu; locally the line shows SKIPPED, not PASSED.

**C13** - `.git` as a file: exit 1, stderr has `git worktrees are not supported by hooks install yet`, no file changes (AC 13)
Proof: run feature | test_worktree_is_refused_before_writing PASSED
Fail first: at f898c82d it exits 0 and modifies the config.

**C15** - No `repos` key, a flow list with items, or unparseable YAML: exit 1, stderr has `cannot add AXON entries to .pre-commit-config.yaml`, config bytes unchanged (AC 15)
Proof: run feature | test_unmergeable_config_exits_1[no-repos-key] PASSED
Proof: run feature | test_unmergeable_config_exits_1[flow-list] PASSED
Proof: run feature | test_unmergeable_config_exits_1[invalid-yaml] PASSED
Fail first: at f898c82d the entry is appended and it exits 0.

### S3 - doctor sees pre-commit repos

**C9** - `_onboarded_repos` includes a registered repo with a `--hook-type=post-commit` driver and `axon-post-commit` in `.yaml` or `.yml`; excludes it when either is absent (AC 9)
Proof: run feature | test_precommit_repo_is_onboarded[yaml] PASSED
Proof: run feature | test_precommit_repo_is_onboarded[only-yml] PASSED
Proof: run feature | test_precommit_repo_is_onboarded[driver-without-hook-type] PASSED
Proof: run feature | test_precommit_repo_is_onboarded[config-without-axon] PASSED
Fail first: at f898c82d the included cases are excluded; the two excluded cases are guards.

**C10** - Entry interpreter `/nonexistent/python`: `check_hook_interpreters` is FAIL, names the repo, suggests the exact pre-commit text without `axon install-hooks`; a mix shows both texts (AC 10)
Proof: run feature | test_broken_precommit_interpreter_fails_with_precommit_suggestion[entry-before-id] PASSED
Proof: run feature | test_broken_precommit_interpreter_fails_with_precommit_suggestion[double-quoted] PASSED
Proof: run feature | test_broken_mix_shows_both_suggestions PASSED
Proof: run feature | test_following_the_suggestion_in_a_yml_repo_yields_a_working_hook PASSED
Fail first: at f898c82d the check says `skipped: no onboarded repos`. The amended command (`--config`, see plan.md) was red first at `cd77294`: following the suggestion in a `.yml`-only repo left a hook that did not run the entry.

**C17** - Invalid YAML, a non-UTF-8 byte or a hook without `entry`: `_hook_interpreter` returns `None` without raising and the repo is listed as unparseable with the pre-commit suggestion, without `axon install-hooks` (AC 17)
Proof: run feature | test_unparseable_precommit_config_is_reported_not_raised[invalid-yaml] PASSED
Proof: run feature | test_unparseable_precommit_config_is_reported_not_raised[non-utf8] PASSED
Proof: run feature | test_unparseable_precommit_config_is_reported_not_raised[no-entry] PASSED
Fail first: at f898c82d the repo is not onboarded.

**C11** - With toolchain `NONE`, a failing registry write keeps `install_hooks` returning the hook names without raising (AC 11)
Proof: run feature | test_registry_write_failure_never_breaks_the_install PASSED
Fail first: guard, green at f898c82d. Deliberate break: remove the `try` around `register_repo` in `install_hooks`, see it red, revert.

**C12** - Without `--apply`: exit 0, the unchanged `dry_run_message`, then `would run: pre-commit install --config .pre-commit-config.yaml --hook-type post-commit --hook-type post-merge`, the "already" line when entries exist, nothing written; `--apply` prints no `would run:` (AC 12)
Proof: run feature | test_dry_run_for_pre_commit_framework PASSED
Proof: run feature | test_dry_run_reports_entries_already_present PASSED
Proof: run feature | test_apply_output_has_no_would_run_line PASSED
Proof: run feature | test_dry_run_mentions_path PASSED
Fail first: at f898c82d the preview never mentions `pre-commit install`; the `--apply` and `TestDryRun` tests are guards.

## Coverage

| Set (size) | Member -> proof | Unproven |
| --- | --- | --- |
| acceptance criteria (18) | AC1 C1; AC2 C2; AC3 C3; AC4 C4; AC5 C5; AC6 C6; AC7 C7; AC8 C8; AC9 C9; AC10 C10; AC11 C11; AC12 C12; AC13 C13; AC14 C14; AC15 C15; AC16 C16; AC17 C17; AC18 C18 | - |
| one-way doors (2) | install argv and `cfg.name` C3; quoted `entry` C18 | - |
| config names (2) | `.yaml` C1; `.yml` C3 | - |
| restored files (6) | config C6; post-commit C6; post-merge C6; post-commit.legacy C6; post-merge.legacy C6; registry C6 | - |
| failing steps (5) | merge C15; install C6; verify drivers C8; register C7; verify driver mode C16 | - |
| preflight refusals (3) | worktree C13; missing binary C5; config shape C15 | - |

## Swept

- validation: C15
- failure modes: C6, C7, C8
- idempotency: C2
- authorization: n/a - local CLI acting on the user's own repo, no principal to check
- concurrency: n/a - one interactive command (`--apply` refuses without a TTY), no shared writer is introduced
- data lifecycle: C4, C6
- dependency failure: C5, C6
- state transitions: C1, C2, C8
- observability: C6, C10, C17

## Handoff

- **Settled mid-build:** C10 / AC 10 amended. 2026-11-27 - Sammy (via Parker 2057651e, relayed by Otto): "Sim, mudar (Recommended)", answering "mudar o critério AC10 do plano F0.1 para o doctor sugerir `pre-commit install --config <arquivo> --hook-type post-commit --hook-type post-merge`?"
- **Dispatched:** none (no Verifier sub-agent; review and acceptance happen on the pull request)
- **Boundary:** C1-C18 closed at `0b39982`; built and run on Windows, Python 3.12, real `pre-commit`
- **Red at base:** the final test files run on `f898c82d` sources gave 32 failed, 29 passed, 3 skipped. Green there, as
  guards: the C9 excluded cases, `TestDryRun`, the older `TestMergeInto` tests, `tests/doctor/..._registry.py` (C11), and
  `test_apply_in_tty_mutates_pre_commit_config` (the second C1 proof, with its stub; C1's red is the first proof)
- **Deliberate breaks, reverted:** C11 - without the `try` around `register_repo`, 2 registry tests fail. C3 - passing
  `str(cfg)` to the install left `test_yml_config_is_passed_by_name` green: pre-commit 4.7.0 itself makes `--config`
  repo-relative (`pre_commit/main.py:178,191`), so the driver holds the file name either way; C3's red is the base run
- **Not run locally:** C16 and the symlink restore test skip on Windows; CI (ubuntu) proves them. The C16 proof line has
  no pass marker, so read the CI log, not a local SKIPPED line. `check_proofs.py` was not run for that reason
- **Beyond the checks:** the user's list indentation, line endings and trailing comments are kept; a symlinked hook is
  restored as a symlink
- **Abandoned:** none
- **Walked:** n/a - no screen
