## Validation: issue #92 — PASS
Spec-anchored check: no spec.md for this issue (entered `task` directly) — fell back to "assertion exists and covers the criterion". Both decided scope items are covered: (1) structural delimiters + framing — tests/adr/test_prompt_injection_fencing.py asserts the fence tags, the payload's containment inside them, the framing sentence preceding the open tag, and forged-close-tag neutralisation in both cases; (2) provenance flag — asserted at the model default, the inference path, draft promotion, the Postgres round-trip/retrofit, and all three consumers (get_adrs, adr list, vault export).
Mutation sensor (mandatory): EMPTY_RETURN=KILLED, IDENTITY_RETURN=KILLED, NEGATE_CONDITIONAL=KILLED, DROP_SIDE_EFFECT=KILLED
Mutation sensor (extras): 4 injected, 4 killed, 0 survived
Report: .specs/features/92-adr-inference-provenance/validation.md

### Detail

Baseline before mutating: 13 passed across the five new test files.

| # | Operator | Mutation | Result |
|---|---|---|---|
| 1 | EMPTY_RETURN | `_fence()` (inference.py) returns `""` | KILLED — test_prompt_injection_fencing (2 tests) |
| 2 | IDENTITY_RETURN | `_fence()` returns `text` unchanged | KILLED — test_prompt_injection_fencing (2 tests) |
| 3 | NEGATE_CONDITIONAL | `provenance != "human"` → `== "human"` in all three consumers (mcp/server.py:747, cli/pb.py:1356, cli/pb.py:1464) | KILLED — 3 tests (get_adrs, adr list, vault export) |
| 4 | DROP_SIDE_EFFECT | Remove the `ALTER TABLE adr ADD COLUMN IF NOT EXISTS provenance` retrofit from `ensure_schema()` | KILLED — 3 tests in test_adr_provenance_column.py |

Extras (risk_area_hit: this is a security issue; the risk area is the provenance flag failing open and the fence failing to neutralise a forged tag). No `mutmut`/`cosmic-ray` configured in the repo, so extras were hand-injected.

| # | Operator | Mutation | Result |
|---|---|---|---|
| E1 | EXCEPTION_SWALLOW-analogue (defence disabled) | `_fence()` drops the forged-tag escaping (`safe = text`) | KILLED — test_forged_closing_tag_cannot_terminate_the_span |
| E2 | fail-open default | `ADR.provenance` default flipped `"llm-inferred"` → `"human"` | KILLED — 4 tests (inference labelling, draft promotion, PG round-trip, get_adrs) |
| E3 | dropped explicit label | `provenance="human"` removed from the MCP `save_adr` write | KILLED — test_save_adr_tool_records_human_provenance |
| E4 | read path always trusts | PG `get_adrs` hardcodes `provenance="human"` instead of reading the column | KILLED — test_provenance_round_trips_through_save_and_get |

Mutations were applied and reverted with in-place file edits only; `git checkout --` was never used. Post-battery the worktree diffstat is byte-identical to the pre-battery baseline and all 13 tests pass.
