# Review — issue #92 (ADR inference provenance + prompt fencing)

Verdict: APPROVE

## Structural pre-check
`git diff master...HEAD` is empty (work is uncommitted); reviewed the working-tree
diff instead. No existing test file is modified or deleted — five new files only
(`tests/adr/test_adr_provenance.py`, `tests/adr/test_prompt_injection_fencing.py`,
`tests/cli/test_adr_provenance_rendering.py`, `tests/mcp/test_get_adrs_provenance.py`,
`tests/store/test_adr_provenance_column.py`). No STOP condition.

## Criterion coverage
1. **Structural delimiters + "data, not directives" framing** — `_fence()`
   (`src/axon/adr/inference.py:225`) wraps both untrusted spans in named tags and
   neutralises a forged closing tag; `templates/adr_classifier.txt` states the blocks
   are data and that only instructions outside them are obeyed. Asserted by
   `test_untrusted_spans_are_delimited_and_framed` and
   `test_forged_closing_tag_cannot_terminate_the_span`;
   `test_format_still_renders_the_json_example` guards the template's other placeholders.
2. **Provenance flag that holds** — `ADR.provenance: Literal["human","llm-inferred"]`
   defaults to `"llm-inferred"`, i.e. fail-closed. Verified every ADR construction site:
   inference (`inference.py:176`), the pending-drain sink (`session_store.py:289`) and
   draft promotion (`pb.py:1732`) all leave the default; only the two human-authored
   entry points (`mcp/server.py:774` `save_adr`, `pb.py:1390` `adr add`) set
   `"human"`. `test_inference_never_writes_human_provenance` and
   `test_full_injection_compliance_still_lands_labelled_machine_inferred` prove the flag
   survives a prompt that fully complies with an injected instruction — this is the
   right test, because it asserts the property that degrades gracefully when the
   framing fails.
3. **Consumers label it** — `get_adrs`, `axon adr list` and the vault export
   (`adr sync`) all emit `ADR_INFERRED_NOTICE`, each with its own test. `ask` and
   `export_adr` were checked and do not surface `ADR` rows (the latter takes a
   `Decision`), so no gap there.

## Persistence
The retrofit is a standalone `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` rather than an
inline column in `CREATE TABLE IF NOT EXISTS` — the exact precedent the rubric flags,
and `test_ensure_schema_retrofits_provenance_onto_a_preexisting_table` pins it against a
pre-created table. The `ON CONFLICT DO NOTHING ... RETURNING id` path keeps its fallback
SELECT (no `or 0`), covered by `test_conflicting_insert_still_returns_the_existing_id`.
Backfilling existing rows to `llm-inferred` is the safe direction, and the notice is
worded "não confirmado como autoria humana", which is true of every row it prints on —
that wording is load-bearing and correctly reasoned in the comment.

## Non-blocking notes
- `getattr(adr, "provenance", "llm-inferred")` at the three render sites is defensive
  against a Pydantic field that always exists; harmless (tolerates duck-typed fakes),
  but it is dead branching.
- No DB-level CHECK on `provenance`; a hand-written out-of-range value would surface as
  a Pydantic `ValidationError` on read rather than a write-time rejection. Acceptable —
  no writer produces one.
- `_fence` neutralises only the closing tag, not a forged opening tag. Correct choice:
  an extra opening tag cannot end the span, so it buys nothing.

Scope is respected: no input blocking, no approval gate, no unrequested surface.
Gate facts as supplied: suite 1729 passed / +13, ruff clean on touched files, mutation
battery 0 survivors.
