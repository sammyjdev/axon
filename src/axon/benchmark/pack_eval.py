"""Scoring for pack quality: did the pack reach the files the work touched?

Two numbers, both over basenames so a moved file still matches: whether any
expected file appears at all, and what fraction of them did. Kept free of I/O
so the scoring is testable without a database.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from pathlib import Path

from pydantic import BaseModel, ConfigDict


class PackCase(BaseModel):
    model_config = ConfigDict(frozen=True)

    query: str
    expected_files: list[str]
    decision_id: str


def basenames(paths: Iterable[str]) -> set[str]:
    """Kept for the fixture generator's ambiguity report; NOT used for scoring."""
    return {str(path).rsplit("/", 1)[-1] for path in paths}


def found_expected(expected_files: set[str], hit_paths: list[str]) -> set[str]:
    """Which expected files the hits actually reached.

    A decision records repo-relative paths ("src/axon/store/collections.py");
    the index stores absolute ones. Matching is therefore a path-suffix test
    anchored on a segment boundary.

    Basename matching was the earlier rule and it inflated the score:
    `__init__.py` occurs 64 times in this corpus, so any one of them was
    credited for the specific one a decision named.
    """
    found: set[str] = set()
    for expected in expected_files:
        needle = expected.strip().lstrip("/")
        if not needle:
            continue
        for hit_path in hit_paths:
            candidate = str(hit_path).strip()
            if candidate == needle or candidate.endswith("/" + needle):
                found.add(expected)
                break
    return found


def pack_hit(expected_files: set[str], hit_paths: list[str]) -> bool:
    return bool(found_expected(expected_files, hit_paths))


def pack_coverage(expected_files: set[str], hit_paths: list[str]) -> float:
    if not expected_files:
        return 0.0
    return len(found_expected(expected_files, hit_paths)) / len(expected_files)


#: _build_context_pack writes one "Arquivo: <path>" header line per segment and
#: flattens newlines out of the excerpt, so an anchored match cannot pick up the
#: same word from inside the content.
_SEGMENT_PATH_RE = re.compile(r"^Arquivo: (.+)$", re.MULTILINE)


def segment_file_paths(segments: Sequence[str]) -> list[str]:
    """File paths of an assembled pack's segments, in order.

    Scores what actually leaves _build_context_pack, which cuts the hit list at
    strategy.max_segments and strategy.max_chars (issue #178).
    """
    paths: list[str] = []
    for segment in segments:
        paths.extend(match.group(1).strip() for match in _SEGMENT_PATH_RE.finditer(segment))
    return paths


def load_cases(path: Path) -> list[PackCase]:
    """Read a case fixture, with or without the provenance wrapper.

    The wrapped shape is {"provenance": {...}, "cases": [...]}; the bare list is
    the original format and still loads, so an older committed fixture keeps
    working.
    """
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload["cases"] if isinstance(payload, dict) else payload
    return [PackCase(**row) for row in rows]
