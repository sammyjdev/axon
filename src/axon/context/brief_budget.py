"""Greedy byte packer for the session brief (CTX-06, CTX-08).

Items stay whole and in the order they are given. The overflow line is
reserved before an item is accepted, so the line itself never pushes the
result over the budget. An item larger than the budget is counted in the
overflow and is not cut.
"""

from __future__ import annotations

BRIEF_BUDGET = 1536
OVERFLOW_TEMPLATE = "+{n} more via axon_get_context"


def overflow_line(count: int) -> str:
    """The one line the brief emits when items do not fit."""
    return OVERFLOW_TEMPLATE.format(n=count)


def pack_items(items: list[str], budget: int) -> str:
    """Pack a prefix of ``items`` into ``budget`` UTF-8 bytes.

    The result is empty when ``items`` is empty. Otherwise it is the kept
    items, one per line, and ``+N more via axon_get_context`` when any item
    was left out. A trailing newline is included when the result is non-empty.
    """
    if not items:
        return ""

    def rendered(chosen: list[str], left: int) -> str:
        lines = list(chosen)
        if left:
            lines.append(overflow_line(left))
        return "\n".join(lines) + "\n"

    chosen: list[str] = []
    for index, item in enumerate(items):
        left_if_taken = len(items) - (index + 1)
        candidate = rendered(chosen + [item], left_if_taken)
        if len(candidate.encode("utf-8")) <= budget:
            chosen.append(item)
            continue
        break
    left = len(items) - len(chosen)
    return rendered(chosen, left)
