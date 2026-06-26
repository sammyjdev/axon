"""Structure-aware Markdown chunker.

See docs/superpowers/specs/2026-06-25-md-chunking-standard-design.md.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from axon.embedder.tokens import estimate_tokens

if TYPE_CHECKING:
    from axon.embedder.chunker import Chunk

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
# carry the prior atom only if it fits in ~48% of the active token budget,
# leaving headroom for the next window; checked as combined size (Bug 1 fix).
_OVERLAP_CARRY_RATIO = _OVERLAP * 4
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


def _is_table_block(block: str) -> bool:
    rows = [ln for ln in block.splitlines() if ln.strip()]
    return bool(rows) and all(ln.lstrip().startswith("|") for ln in rows)


def _atoms(text: str, max_tokens: int = MAX_TOKENS) -> list[str]:
    """Break text into the smallest units we are willing to keep whole."""
    atoms: list[str] = []
    for block in re.split(r"\n\s*\n", text):
        if not block.strip():
            continue
        if _is_table_block(block) or estimate_tokens(block) <= max_tokens:
            atoms.append(block)  # paragraph / table kept whole (tables are always atomic)
            continue
        for sent in _SENTENCE_RE.split(block):  # paragraph too big -> sentences
            if estimate_tokens(sent) <= max_tokens:
                atoms.append(sent)
            else:
                # Sentence too big -> accumulate words until the token budget is full.
                words = sent.split()
                buf: list[str] = []
                for word in words:
                    if buf and estimate_tokens(" ".join(buf + [word])) > max_tokens:
                        atoms.append(" ".join(buf))
                        buf = []
                    buf.append(word)
                    # A single word that exceeds the budget is emitted as-is (best-effort:
                    # cannot split further without breaking mid-token boundaries).
                    if not buf[1:] and estimate_tokens(buf[0]) > max_tokens:
                        atoms.append(buf.pop())
                if buf:
                    atoms.append(" ".join(buf))
    return atoms


def split_text(text: str, max_tokens: int = MAX_TOKENS) -> list[str]:
    if estimate_tokens(text) <= max_tokens:
        return [text]
    atoms = _atoms(text, max_tokens)
    windows: list[str] = []
    cur: list[str] = []
    for atom in atoms:
        candidate = cur + [atom]
        if cur and estimate_tokens("\n\n".join(candidate)) > max_tokens:
            windows.append("\n\n".join(cur))
            # overlap: carry the last atom into the next window only if the
            # combined [overlap_atom, atom] still fits within the budget.
            overlap_atom = cur[-1]
            combined = "\n\n".join([overlap_atom, atom])
            cur = [overlap_atom, atom] if estimate_tokens(combined) <= max_tokens else [atom]
        else:
            cur = candidate
    if cur:
        windows.append("\n\n".join(cur))
    return windows or [text]


def _breadcrumb(stem: str, heading_path: tuple[str, ...]) -> str:
    return " > ".join([stem, *heading_path])


def _group_heading_path(group: list[Section]) -> tuple[str, ...]:
    """Return the heading_path to use as breadcrumb for a packed group.

    If every section's heading_path is a prefix of the last section's path
    (a pure descending chain), return the last (deepest) path to preserve
    the existing specific-symbol behavior.

    Otherwise (siblings packed together), return the longest common prefix
    across all sections so the breadcrumb is not misleadingly specific.
    """
    last_path = group[-1].heading_path
    # Check whether all paths are prefixes of the last (descending chain).
    if all(last_path[: len(sec.heading_path)] == sec.heading_path for sec in group):
        return last_path
    # Siblings case: compute longest common prefix.
    if not group:
        return ()
    common = list(group[0].heading_path)
    for sec in group[1:]:
        path = sec.heading_path
        common = [c for i, c in enumerate(common) if i < len(path) and path[i] == c]
    return tuple(common)


def chunk_markdown(source: str, file_path: str) -> list[Chunk]:
    from axon.embedder.chunker import Chunk  # local import avoids cycle

    stem = Path(file_path).stem
    sections = parse_sections(source)
    if not sections:
        return [
            Chunk(symbol=stem, chunk_type="section", start_line=1, end_line=1,
                  content="", file_path=file_path, language="markdown")
        ]

    chunks: list[Chunk] = []
    for group in pack_sections(sections):
        first = group[0]
        crumb = _breadcrumb(stem, _group_heading_path(group))
        body = "\n".join(ln for sec in group for ln in sec.lines)
        start = first.start_line
        end = group[-1].start_line + len(group[-1].lines) - 1
        crumb_tokens = estimate_tokens(f"{crumb}\n\n")
        # Subtract 1 extra token to absorb floor-truncation: int(a)+int(b) can be
        # 1 less than int(a+b) when fractional parts sum to >= 1.
        body_budget = max(MIN_TOKENS, MAX_TOKENS - crumb_tokens - 1)
        windows = split_text(body, body_budget)
        for i, win in enumerate(windows):
            symbol = crumb if len(windows) == 1 else f"{crumb}[{i}]"
            chunks.append(
                Chunk(symbol=symbol, chunk_type="section",
                      start_line=start, end_line=end,
                      content=f"{crumb}\n\n{win}", file_path=file_path,
                      language="markdown")
            )
    return chunks
