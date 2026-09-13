### Task 5: derive embeddings.project from the repo root

`project` currently holds a directory-name fragment, so it is not a project
identifier at all. The table already stores `file_path`, so existing rows can be
re-keyed with an UPDATE that derives the root - **no re-embedding of the 24,164
vectors is required**. Change the derivation at write time and write the backfill in the
same task.

  - Depends on: Task 3
  - Acceptance: new writes derive `project` from the repo root; the backfill exists as a
    dry-run-by-default script, proven against an ephemeral Postgres seeded with rows from
    the mixed projects (including a `project='tests'` row set spanning more than one
    repo), after which no `project` maps to more than one repo root and a `search_code`
    scoped to one project returns no chunk whose `file_path` lies outside it. Applying it
    to the 24,164 live rows is the operator's run.
