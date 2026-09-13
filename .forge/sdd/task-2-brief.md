### Task 2: add a purge script for the fixture rows and the test-written handoff briefs

A one-off cleanup with a dry run by default, because it touches the live store and the
operator's vault repo. Fixture summaries to match: `a decision`, `first decision`,
`add redis cache`, `drop neo4j backend`, `adopt sqlite graph`. Test-written briefs are
the ones with no `## From this session` section - the real ones always carry caller
notes.

  - Depends on: Task 0
  - Acceptance: `scripts/purge_test_artifacts.py` with no flags lists the matching rows
    and briefs and changes nothing; `--apply` is refused without an explicit scope flag;
    against an ephemeral Postgres and a temp vault seeded with those fixture summaries
    and briefs, `--apply` leaves 0 matching rows and every remaining brief has a
    `## From this session` section. Running it against the operator's live store and
    vault is the operator's, outside the loop.

