"""Drop pack slots that carry nothing new.

Measured 2026-08-29 over 40 questions: 10.0% of slots held byte-identical
content and 13.4% repeated a file. Small on its own; the point is that those
slots are the ones the reranker wants.
"""

from __future__ import annotations

_DEFAULT_MAX_PER_FILE = 2


def dedup_hits(hits: list[dict], *, max_per_file: int = _DEFAULT_MAX_PER_FILE) -> list[dict]:
    """Keep the first occurrence of each content, at most max_per_file per file.

    Hits arrive ranked, so first-wins keeps the better-ranked copy. Order is
    preserved: this drops slots, it never reorders them.

    When nothing is dropped, the input list is returned unchanged, so a caller
    that gets its own list back must not mutate the result in place. This keeps
    a pack with no duplicates byte-for-byte and object-for-object the pack the
    store returned.
    """
    seen_content: set[str] = set()
    per_file: dict[str, int] = {}
    kept: list[dict] = []
    for hit in hits:
        payload = hit.get("payload") or {}
        content = str(payload.get("content", ""))
        file_path = str(payload.get("file_path", ""))
        if content in seen_content:
            continue
        if per_file.get(file_path, 0) >= max_per_file:
            continue
        seen_content.add(content)
        per_file[file_path] = per_file.get(file_path, 0) + 1
        kept.append(hit)
    return hits if len(kept) == len(hits) else kept


