"""L3 polarity gate (dec-111).

Validates that the ADR's key terms have a grep-match in the
``diff ∪ commit_msg_body``. Where L2 measures overall overlap, L3
ensures the *specific* nouns/identifiers carrying the decision's
meaning are anchored in observable change.

Key terms come from the ADR title and decision fields (these are the
shortest and most concept-dense parts of the ADR). At least one
key-term-with-substance (>= 3 chars, not a stopword) must appear in
the pool.
"""

from __future__ import annotations

from axon.adr.gates.l2 import tokenize


def passes_l3(
    title: str,
    decision: str,
    *,
    pool_text: str,
    required: bool = True,
) -> tuple[bool, list[str]]:
    """Return ``(passed, matched_terms)``.

    If ``required`` is False the gate always passes; useful for
    operators who want to disable polarity check via config.
    """
    if not required:
        return True, []

    pool_tokens = set(tokenize(pool_text))
    key_tokens = set(tokenize(title)) | set(tokenize(decision))
    matched = sorted(key_tokens & pool_tokens)
    return bool(matched), matched
