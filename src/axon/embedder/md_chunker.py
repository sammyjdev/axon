"""Structure-aware Markdown chunker (see docs/superpowers/specs/2026-06-25-md-chunking-standard-design.md)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

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
                words = sent.split()  # sentence too big -> word windows
                step = max(1, int(max_tokens / 0.35 / 6))  # ~chars->words budget
                for i in range(0, len(words), step):
                    atoms.append(" ".join(words[i : i + step]))
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
            # overlap: carry the last atom into the next window
            overlap_atom = cur[-1]
            cur = [overlap_atom, atom] if estimate_tokens(overlap_atom) <= max_tokens * _OVERLAP_CARRY_RATIO else [atom]
        else:
            cur = candidate
    if cur:
        windows.append("\n\n".join(cur))
    return windows or [text]


def _breadcrumb(stem: str, heading_path: tuple[str, ...]) -> str:
    return " > ".join([stem, *heading_path])


def chunk_markdown(source: str, file_path: str):
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
        lead = group[0]
        # Use the last section's heading_path: it is the deepest / most specific
        # heading in the packed group, which makes the symbol unambiguous when
        # sections with the same top-level heading are merged.
        crumb = _breadcrumb(stem, group[-1].heading_path)
        body = "\n".join(ln for sec in group for ln in sec.lines)
        start = lead.start_line
        end = group[-1].start_line + len(group[-1].lines) - 1
        crumb_tokens = estimate_tokens(f"{crumb}\n\n")
        body_budget = max(MIN_TOKENS, MAX_TOKENS - crumb_tokens)
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
