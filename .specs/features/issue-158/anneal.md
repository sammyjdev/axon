## Anneal: issue-158

- Lesson: ruff anchors an S-family diagnostic to the ARGUMENT LIST of a multi-line call, not to the line the call starts on, so `subprocess.check_output(  # noqa: S603, S607` suppresses nothing. Four occurrences in one pass. Remedy: read the `-->` line:col in ruff's own output and put the directive there, or collapse the call onto one line.
  Destination: craft lesson
  Call status: skipped: capability not bound (record_lesson is declared in memory.json but absent from this harness's tool list)
- Lesson: FORGE contradicts itself on whether a maker may APPEND to an existing test file: task.md pre-check 0 blocks ANY modification, while the tier profiles permit 'writing new tests'. The planner planned appends, one reviewer approved the +118/-0 diff and the other BLOCKed it, costing a fix round. Filed as sammyjdev/claude-skills#33 with a content-based fix (block only removed/changed lines, which is what the rule actually protects).
  Destination: loop issue
  Call status: n/a: not routed through record_lesson
- Lesson: A backfill that re-keys rows must be default-safe. `rekey-repo` originally moved every row whose git_hash was merely PRESENT in the target checkout, which would silently re-key a legitimately-keyed row that shares history. Object presence is not proof of mis-filing. Proposed axon RULES.md invariant: a migration command requires an explicit scope (--only-key or --all) before it may write, and its dry run must print the grouped source keys it would move.
  Destination: repo invariant
  Call status: n/a: not routed through record_lesson
