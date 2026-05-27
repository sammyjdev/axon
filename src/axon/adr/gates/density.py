"""Density gate (dec-111).

Three-check anti-boilerplate filter applied after L1-L3:

1. **Architectural lexicon hit**: at least one token from the lexicon
   appears in the rationale that does NOT appear in the diff. This
   proves the text is genuine commentary, not a paraphrase of the diff.
2. **Overlap ratio cap**: rejects ADRs whose rationale is a copy-paste
   of the diff (>70% of rationale tokens are literal substrings of the
   diff).
3. **Denylist** (handled in L2's tokenizer): boilerplate tokens (JIRA
   ids, GitHub trailers, conventional commit types) do not contribute
   to any count.

In structural mode (dec-111 detector hit), check 1 is dropped and the
overlap ratio cap is raised — structural refactor rationale must mirror
the diff by nature.
"""

from __future__ import annotations

from axon.adr.gates.l2 import tokenize
from axon.adr.lexicon import default_lexicon


def passes_density(
    rationale: str,
    *,
    diff: str,
    structural_mode: bool = False,
    overlap_ratio_cap: float = 0.7,
    overlap_ratio_cap_structural: float = 0.9,
    lexicon: frozenset[str] | None = None,
) -> tuple[bool, dict[str, object]]:
    """Return ``(passed, details)``.

    ``details`` includes the measured ratio, lexicon hits, and which
    sub-check failed (if any).
    """
    lex = lexicon if lexicon is not None else default_lexicon()
    rationale_tokens = tokenize(rationale)
    diff_token_set = set(tokenize(diff))

    # Check 2: overlap ratio cap
    ratio = _overlap_ratio(rationale_tokens, diff_token_set)
    cap = overlap_ratio_cap_structural if structural_mode else overlap_ratio_cap
    if ratio > cap:
        return False, {
            "reason": "overlap_ratio_exceeds_cap",
            "ratio": ratio,
            "cap": cap,
            "structural_mode": structural_mode,
        }

    # Check 1: architectural lexicon hit outside diff (skipped in structural)
    if not structural_mode:
        rationale_set = set(rationale_tokens)
        lex_hits_in_rationale = rationale_set & lex
        lex_hits_outside_diff = lex_hits_in_rationale - diff_token_set
        if not lex_hits_outside_diff:
            return False, {
                "reason": "no_architectural_lexicon_outside_diff",
                "lex_hits_in_rationale": sorted(lex_hits_in_rationale),
                "structural_mode": structural_mode,
            }
        return True, {
            "ratio": ratio,
            "lex_hits_outside_diff": sorted(lex_hits_outside_diff),
            "structural_mode": structural_mode,
        }

    return True, {"ratio": ratio, "structural_mode": True}


def _overlap_ratio(rationale_tokens: list[str], diff_set: set[str]) -> float:
    if not rationale_tokens:
        return 0.0
    overlap = sum(1 for t in rationale_tokens if t in diff_set)
    return overlap / len(rationale_tokens)
