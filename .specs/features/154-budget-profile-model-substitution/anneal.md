## Anneal: 154-budget-profile-model-substitution

- Lesson: A routing profile's three tier rungs must be three pairwise-distinct model ids. engine._fallback_model_for is a tier downgrade reused as the completion failover (issue #100) and returns None when the candidate equals the primary, so pointing two rungs at one id silently disables that tier's fallback with no error. The plan for #154 did exactly that and broke 8 existing tests in test_engine_completion_fallback{,_usage_shape}.py, which only pinned the invariant indirectly inside individual test bodies. Now asserted directly for every profile by test_every_profile_has_three_distinct_tier_rungs.
  Destination: repo invariant
  Call status: n/a: not routed through record_lesson
- Lesson: A provider_for_model prefix branch must match a slash-terminated prefix and needs a negative test for a lookalike. The function falls through to 'anthropic' for anything unrecognised, so startswith('deepinfra') instead of startswith('deepinfra/') misroutes deepinfrax/... into that provider's rate-limit bucket and past its enabled gate with the suite green. The mutation sensor found this branch surviving until test_provider_for_model_requires_the_slash_boundary was added; the five pre-existing prefix branches still have no such test.
  Destination: repo invariant
  Call status: n/a: not routed through record_lesson
- Lesson: Backticks inside a `python3 -c "..."` step in a CI workflow are shell command substitution, not markup: a Python comment written with backticks around the words free and budget made the shell run `free` and `budget`. Harmless on macOS (command not found, exit 0), fatal on ubuntu-latest where free is procps and its multi-line output is spliced into the source. Verify a workflow's inline script by extracting it from the yaml and running it, not by reading it. Same block: every line of an inline -c script needs identical leading whitespace or Python raises IndentationError: unexpected indent.
  Destination: craft lesson
  Call status: skipped: capability not bound
- Lesson: An inline # comment on a value line in a dotenv file can become part of the value: KEY=free # note is stripped by python-dotenv but not by export $(cat .env | xargs). Comments go on their own line above the assignment.
  Destination: craft lesson
  Call status: skipped: capability not bound
