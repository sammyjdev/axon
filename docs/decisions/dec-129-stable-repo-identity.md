# dec-129 - Stable repo identity

- Status: accepted
- Date: 2026-08-26

## Context

Git hooks used the checkout directory basename as `Decision.repo`. A main checkout
named `axon` and a linked worktree named `agent-issue-158` therefore wrote to different
decision streams. Those worktree decisions were omitted from normal repo recall and
their low per-worktree counts prevented the scope-end threshold from triggering scoring
and export. The same path value was incorrectly used as an identity at 12 sites.

On this machine, git reports:

```
# normal checkout
git rev-parse --git-common-dir
.git

# linked worktree
git rev-parse --git-common-dir
/Users/samdev/dev/axon/.git

# subdirectory of a normal checkout
git -C src/axon/hooks rev-parse --git-common-dir
../../../.git
```

## Decision

Use the parent repository's `.git` directory name as the stable, bare repo identity.
`repo_identity()` obtains it with `git -C <path> rev-parse --git-common-dir`, resolves
the result against the supplied path, and returns the parent directory name when the
result is `.git`. `Path` joining handles both relative normal-checkout output and
absolute worktree output. Bare repositories and separate git directories use the git
directory name without its `.git` suffix.

If git cannot resolve the directory, fall back to its basename. This preserves existing
behaviour for non-git directories and for git failures such as a safe-directory refusal.

The migration command re-keys an existing decision only when its `git_hash` is reachable
from the supplied checkout. It updates rows in place and preserves all other fields.

## Rejected

- Origin remote slug would re-key the existing correct bare-name rows, fails for repos
  without a remote, and still collides when reduced to a basename.
- `git worktree list --porcelain` returns the same identity through a multi-record format
  instead of one git value, so it adds parsing without value.

## Unchanged on purpose

- `_repo_root` remains a path helper. Git operations still need the actual checkout path.
- The read side needs no change after issue #146.
- `Decision.repo` remains a bare name.
- `judged` remains the canonical scored flag under dec-121. `validation_score == 0.0`
  remains a valid judged score.

## Consequences

A dry run against the live Postgres store on 2026-08-26 measured the actual population:

```
axon rekey-repo ~/dev/axon
```

reported 102 decisions to re-key across 31 distinct source keys, not 25. FORGE worktree
names have changed shape over time (`agent-issue-*`, `agent-plan-*`,
`axon-promotion-*`, `degrau0-*`, `benchmark-*-security`, plus one-off names like
`fix-platform-gpu`), so no single glob covers the real population: `--only-key
'agent-*'` reaches only about half of the 102 rows (52).

Operators migrate the known worktree rows by running the dry run first, reading its
grouped per-key summary, then applying with `--all` once that list has been eyeballed:

```
axon rekey-repo ~/dev/axon                 # dry run, read the grouped key summary
axon rekey-repo ~/dev/axon --apply --all   # after confirming the list above
axon rekey-repo ~/dev/gnomon-eval
axon rekey-repo ~/dev/gnomon-eval --apply --all
```

`--only-key` remains available as the narrower option for moving one key at a time.

The gnomon-eval figure (about 8 rows across 6 keys) is still an estimate: the dry run
above was only executed against `~/dev/axon`, not `~/dev/gnomon-eval`. PitStopOS rename
drift is a separate cause and remains unchanged. Dry runs preview every provably
reachable source key. Applying requires an explicit `--only-key` glob or `--all`, so a
shared commit hash cannot move a legitimately keyed row by default. `git cat-file -e`
against a candidate hash proves the object is present in the target repo, not that the
row was mis-filed under its current key - that gap is exactly why `--apply` refuses to
run without an explicit `--only-key` or `--all`.
