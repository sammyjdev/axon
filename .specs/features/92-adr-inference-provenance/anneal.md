## Anneal: 92-adr-inference-provenance

- Lesson: A sandboxed shell makes a testcontainers suite report ordinary failures rather than skips. The docker socket raises PermissionError(1, 'Operation not permitted'), and the run summarised as '118 failed, 1500 passed, 92 errors' on a tree whose real state was '1716 passed'. pytest.importorskip('testcontainers.postgres') guards the PACKAGE, not the DAEMON, so the tests ERROR instead of skipping. Read one failing test's traceback before ever calling a baseline red. Appended to craft-lessons.md under ## pytest.
  Destination: craft lesson
  Call status: skipped: capability not bound
- Lesson: Every models.json handle pins to codex or opencode. Both rails were down at once (codex quota, opencode server errors), taking out legendary.plan, legendary.exec, quench.mutator and both reviewers - while role_candidates.json held measured, live alternatives on the agy and anthropic rails the whole time. The loop should walk the handle's role and re-pin to the highest-ranked live candidate instead of failing; the orchestrator had to do it by hand via --handle and FORGE_RESOLVE_HANDLE. Filed as sammyjdev/claude-skills#34.
  Destination: loop issue
  Call status: n/a: not routed through record_lesson
