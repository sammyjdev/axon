# Code Quality Review: ADR Inference Provenance

## Verdict
Verdict: APPROVE

## Reasoning
The executor successfully implemented the required mitigations for the ADR inference prompt injection vulnerability.

1. **Structural Delimiters and Framing**:
   - `src/axon/adr/inference.py` introduces `_fence`, wrapping untrusted inputs inside `<untrusted_commit_message>` and `<untrusted_diff>`.
   - `_fence` sanitizes forged closing tags using case-insensitive replacement (e.g. `</untrusted_commit_message>` becomes `</untrusted_commit_message_>`), which neutralizes early-termination attacks.
   - `src/axon/templates/adr_classifier.txt` was properly updated to instruct the model to treat text inside the spans as data rather than instructions.

2. **Provenance Flag**:
   - `src/axon/store/session_store.py` adds `provenance: Literal["human", "llm-inferred"]` to the `ADR` model, safely defaulting to `llm-inferred`.
   - `src/axon/store/pg_decision_repository.py` safely adds the `provenance` column with `DEFAULT 'llm-inferred'`, retrofitting existing rows correctly. The unique index and insert/select statements were meticulously updated.
   - The CLI and MCP tools (`src/axon/cli/pb.py` and `src/axon/mcp/server.py`) correctly check this field and append `ADR_INFERRED_NOTICE` when `provenance != "human"`.
   - The MCP `save_adr` tool and CLI `adr add` explicitly tag manually created ADRs with `provenance="human"`.

3. **Testing**:
   - No existing test files were modified, fully respecting the TDD requirements.
   - The five new test files cover schema retrofitting, SQL round-trips, the CLI and MCP surface, and prompt-fencing behaviour, verifying the observable behaviours robustly.

The codebase is correct, maintains high security standards against prompt injection by degrading gracefully through provenance, and preserves backwards compatibility with existing rows in Postgres.
