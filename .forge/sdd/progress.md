# Progress — docs/plans/2026-09-12-axon-isolation-and-keying.md

Ledger for this plan only. It lives under `.forge/sdd/` rather than
`.superpowers/sdd/`, which already holds a 32K ledger from the completed embedder
epic — plan-mode reads `.forge/` first, and one plan's state must never be split
across both.

Task 1: complete (commits d372c38..839ed6d, done by hand outside the loop — no
quench pass, no mutation sensor. Verified end to end instead: a full suite run
leaves the operator's handoff dir, stats.jsonl and records.jsonl byte-identical,
and removing the vault fixture makes the new guard fail and name the file it
wrote. Suite 2085 passed / 7 skipped / 7 xfailed. Closed issue #203.)

Task 0: complete (commits 9e57e16..3fc4e20, review clean - legendary.review.spec
APPROVE, legendary.review.quality APPROVE, mutation sensor 4/4 mandatory KILLED
plus 4/4 extras KILLED at legendary+risk_area_hit). Gate 2088 passed / 7 skipped /
7 xfailed, run green while this session deliberately called an AXON MCP tool
mid-gate - the exact condition that made the baseline red. The conftest edit went
through common.testauthor with recorded provenance, because tests/conftest.py is
an existing test file and only a test-side role may modify one.

Task 2: complete (commits 7d62fcc..4b6d8d6, review clean - legendary.review.spec
APPROVE, legendary.review.quality APPROVE). Gate 2095 passed / 7 skipped / 7
xfailed. Mutation sensor needed two rounds: the first battery left
EXACT_MATCH_PREFIX_LIKE SURVIVING, meaning no test could tell the exact IN match
from a prefix match on a script that deletes decision rows; closed with
test_exact_match_preserves_extended_fixture_summaries and the re-run killed 5/5
extras. The plan's brief selector was corrected in the plan text as well as the
script - axon_handoff omits "## From this session" whenever notes is empty
(server.py:1367), so the original selector would have deleted real briefs. The
conjunctive selector was checked as a pure function against the operator's real
vault: 120 selected, 1 real brief kept, nothing written. --apply against the live
store and vault remains the operator's run, never the loop's.

Task 3: complete (commits f92f09a..7163833, review clean - legendary.review.spec
APPROVE, legendary.review.quality APPROVE). Gate 2121 passed / 7 skipped / 7
xfailed. Mutation sensor 4/4 mandatory KILLED plus 5/5 extras KILLED, first try.
The plan said eight call sites; there are ten. axon_export_now (destructive, names
vault documents after repo) and axon_validation_stats (no normalization and no
default at all) were both missed, and the plan text is corrected. The resolver
normalizes narrowly - only path-looking values - and a test that makes
subprocess.check_output raise pins that narrowness, so a normalize-everything
mutant dies. Carried, not fixed: session_note.project holds caller-supplied repo
values too; both its write paths are fixed here, but pre-existing rows can still
hold absolute paths, so it is a candidate for the operator's re-key run.

Task 5: complete (commits 33dee0e..b180b67, review clean - legendary.review.spec
APPROVE, legendary.review.quality APPROVE). Gate 2137 passed / 7 skipped / 7
xfailed. Mutation sensor needed two rounds: the first left the cache-key mutant
SURVIVING - keying project_by_dir by directory NAME reintroduces the very
repo_a/tests vs repo_b/tests collision this task removes, and the flagship test
missed it because it called index_path once per repo, so the two directories never
shared a cache lifetime. Closed by a test that indexes both repos in one
index_path call; re-run killed 6/6.

The executor's first attempt widened repo_identity so any non-existent path
resolved through its parent: repo_identity("some-other-repo") returned 'axon'
instead of the name, filing a caller's data under the ambient repo with the whole
suite green. Caught by probing the changed function against master rather than by
any test. Narrowed to root.parent only when root.is_file(), pinned by
tests/core/test_repo_identity_nonexistent.py.

FOR THE OPERATOR, before --apply on the 24,164 rows: a row whose directory no
longer exists on disk re-keys to its stale directory fragment, because git cannot
resolve a missing path. The "no project maps to more than one repo root" property
is proven for the seeded case and is NOT guaranteed for orphaned rows. Raised
independently by both reviewers.

Also carried, not fixed: session_note.project holds caller-supplied repo values.
Both write paths were fixed in Task 3, but pre-existing rows can hold absolute
paths, so that table is a candidate for the operator's own re-key round. This pass
was deliberately not widened to a third table.

Cross-review (cross_review: true in loop.yaml): codex/gpt-5.6-terra over the whole
branch diff, after both same-family reviewers had approved every task and the
sensor was clean. Nine findings; each verified by reading the code before acting.
Four were real and are fixed in bda9193: the guard was blind to a deletion and to
a same-size overwrite; all three scripts printed the Postgres password on a
connection failure; the purge followed a symlinked handoffs directory and deleted
files outside the vault; and --only-project '' rekeyed the whole embeddings table
because the empty string is falsy. Report kept at .forge/cross-review/pr-204.md.

Five were not acted on and are recorded in the PR body: symlinked-path write
attribution (already a known residual), import-time writes preceding the snapshot
(pre-existing and deliberate), repo_identity falling back to the worktree name
under a safe.directory refusal (pre-existing on master, already warns), and
unescaped report output. The re-run sensor killed 6/6 extras aimed at disarming
each of the four fixes; legendary.review.quality re-reviewed the fix diff and
returned APPROVE. Gate 2146 passed / 7 skipped / 7 xfailed.

Process note worth keeping: the first attempt at this fix round was dispatched to
legendary.exec, which modified five existing test files. check_test_edits refused
it, correctly - only a test-side role may modify an existing test file. The test
work was reverted and redone through common.testauthor with recorded provenance.
The guard caught an orchestrator mistake, which is what it is for.
