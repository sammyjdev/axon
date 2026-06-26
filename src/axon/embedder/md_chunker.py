"""Structure-aware Markdown chunker (see docs/superpowers/specs/2026-06-25-md-chunking-standard-design.md)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from axon.embedder.tokens import estimate_tokens

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")


@dataclass(frozen=True)
class Section:
    heading_path: tuple[str, ...]  # () for preamble before the first heading
    start_line: int  # 1-based line of the section's first line
    lines: tuple[str, ...]


def parse_sections(source: str) -> list[Section]:
    lines = source.splitlines()
    sections: list[Section] = []
    stack: list[tuple[int, str]] = []  # (level, text)
    cur_path: tuple[str, ...] = ()
    cur_start = 1
    cur_lines: list[str] = []
    in_fence = False

    def flush() -> None:
        if cur_lines:
            sections.append(Section(cur_path, cur_start, tuple(cur_lines)))

    for lineno, line in enumerate(lines, start=1):
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            cur_lines.append(line)
            continue
        m = None if in_fence else _HEADING_RE.match(line)
        if m:
            flush()
            level = len(m.group(1))
            text = m.group(2).strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, text))
            cur_path = tuple(t for _, t in stack)
            cur_start = lineno
            cur_lines = [line]
        else:
            cur_lines.append(line)

    flush()
    return sections


MIN_TOKENS = 128
TARGET_TOKENS = 480
MAX_TOKENS = 512


def _section_text(sec: Section) -> str:
    return "\n".join(sec.lines)


def _top(sec: Section) -> str | None:
    return sec.heading_path[0] if sec.heading_path else None


def pack_sections(sections: list[Section]) -> list[list[Section]]:
    groups: list[list[Section]] = []
    buf: list[Section] = []
    buf_tokens = 0

    def flush() -> None:
        nonlocal buf, buf_tokens
        if buf:
            groups.append(buf)
            buf, buf_tokens = [], 0

    for sec in sections:
        sec_tokens = estimate_tokens(_section_text(sec))
        if not buf:
            buf, buf_tokens = [sec], sec_tokens
            continue
        same_top = _top(sec) == _top(buf[0])
        if same_top and buf_tokens + sec_tokens <= MAX_TOKENS:
            buf.append(sec)
            buf_tokens += sec_tokens
        else:
            flush()
            buf, buf_tokens = [sec], sec_tokens
    flush()
    return groups


_OVERLAP = 0.12
_OVERLAP_CARRY_RATIO = _OVERLAP * 4  # carry the prior atom only if it fits in ~48% of MAX_TOKENS, leaving headroom for the next window
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


def _is_table_block(block: str) -> bool:
    rows = [ln for ln in block.splitlines() if ln.strip()]
    return bool(rows) and all(ln.lstrip().startswith("|") for ln in rows)


def _atoms(text: str) -> list[str]:
    """Break text into the smallest units we are willing to keep whole."""
    atoms: list[str] = []
    for block in re.split(r"\n\s*\n", text):
        if not block.strip():
            continue
        if _is_table_block(block) or estimate_tokens(block) <= MAX_TOKENS:
            atoms.append(block)  # paragraph / table kept whole
            continue
        for sent in _SENTENCE_RE.split(block):  # paragraph too big -> sentences
            if estimate_tokens(sent) <= MAX_TOKENS:
                atoms.append(sent)
            else:
                words = sent.split()  # sentence too big -> word windows
                step = max(1, int(MAX_TOKENS / 0.35 / 6))  # ~chars->words budget
                for i in range(0, len(words), step):
                    atoms.append(" ".join(words[i : i + step]))
    return atoms


def split_text(text: str) -> list[str]:
    if estimate_tokens(text) <= MAX_TOKENS:
        return [text]
    atoms = _atoms(text)
    windows: list[str] = []
    cur: list[str] = []
    for atom in atoms:
        candidate = cur + [atom]
        if cur and estimate_tokens("\n\n".join(candidate)) > MAX_TOKENS:
            windows.append("\n\n".join(cur))
            # overlap: carry the last atom into the next window
            overlap_atom = cur[-1]
            cur = [overlap_atom, atom] if estimate_tokens(overlap_atom) <= MAX_TOKENS * _OVERLAP_CARRY_RATIO else [atom]
        else:
            cur = candidate
    if cur:
        windows.append("\n\n".join(cur))
    return windows or [text]
