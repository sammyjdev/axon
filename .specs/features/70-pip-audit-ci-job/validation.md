## Validation: issue #70 — PASS

Spec-anchored check: 2/2 ACs matched (no `spec.md` for this issue — entered
via `task` directly, not `blueprint` — so verification is via direct command
execution + diff inspection, per the Common-tier non-code exemption for a
CI-config-only issue: no application source code was touched, so there is
no pytest test file to map criteria to).

- AC1 (new job in `ci.yml`, runs on every PR): verified by diff inspection —
  a new top-level `audit` job (`.github/workflows/ci.yml`) sits alongside
  the existing `lint`/`compile`/`test`/`profile-smoke` jobs, with no
  job-level `on:` override, so it inherits the file-level `on: pull_request`
  / `push: [master, main]` triggers.
- AC2 (job is green on current master dependency set, or documents any
  accepted/ignored CVE with justification): verified by building a clean
  venv (NOT the machine's shared/polluted dev venv, which has unrelated
  packages from other projects and produced a misleading 16-finding
  baseline) and running the exact step sequence the new job runs. Initial
  clean-venv run (`pip install -e ".[dev]"` + `pip-audit`, no upgrade step)
  found 5 vulnerabilities, all on the venv-bootstrap `setuptools==65.5.0`
  (PYSEC-2022-43012, PYSEC-2025-49, PYSEC-2026-1918 — fixes available for
  all three, so this is NOT the "no fix available" case the AC allows;
  suppressing them would have been wrong). The shipped job instead upgrades
  `pip`/`setuptools` before installing project deps — re-run after that fix
  returned `No known vulnerabilities found` (exit 0). No `--ignore-vuln` or
  suppression flag exists anywhere in the diff.

Mutation sensor (Common tier: 1 injected, 1 killed, 0 survived):
1. Removed the "upgrade pip and setuptools" step (5 lines: rationale
   comment + step) from `.github/workflows/ci.yml` in the real worktree
   file, then rebuilt a fresh clean venv and re-ran the mutated recipe
   (`pip install -e ".[dev]"` + `pip-audit`, no upgrade). Result: exit 1,
   "Found 13 known vulnerabilities in 2 packages" (`pip` 23.2.1 gains 8
   findings on top of `setuptools`'s 5, since the mutated recipe also skips
   the pip upgrade) — confirming the upgrade step is load-bearing for AC2,
   not decorative. Mutation reverted immediately via `cp` from a pre-mutation
   backup; `git diff --stat` and `git status --short` confirmed byte-identical
   to the committed state afterward — no mutation left in the tree.

Pre-check 0 (test-file structural diff): `git diff <base>...HEAD -- tests/
test/` for this pass's two commits (94f7b25, 12ffb05) is empty — neither
commit touches any test file. (An unrelated new test file,
`tests/expansion/test_extractors_xxe.py`, already existed on this branch
from issue #68's prior work — confirmed via `git diff --stat` scoped to
just this pass's commit range, not the full branch-vs-master range.)

Independent review: 1 Common-tier reviewer (Haiku 4.5, spec-compliance
only) returned APPROVE — both ACs met, diff adds nothing extra (no changes
to `lint`/`compile`/`test`/`profile-smoke` jobs, no changes to
`.pre-commit-config.yaml` — pip-audit is deliberately CI-only per the issue
body, not added to the pre-commit hook), non-code exemption valid.

Gate (`.claude/loop.yaml` `gate_cmd`): ruff clean ("All checks passed!");
pytest 341 passed, 1 skipped, 0 failed, in ~47s. No pre-existing failures
observed this run (unlike issues #67/#68, which saw 6 pre-existing failures
from shared-Postgres contention with a concurrent benchmark arm) — this
pass's diff (CI-workflow + pyproject.toml only) doesn't touch any code path
the gate's test suite exercises, so a clean 0-failure run is consistent with
non-regression either way.

Report: .specs/features/70-pip-audit-ci-job/validation.md
