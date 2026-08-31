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
    return {str(path).rsplit("/", 1)[-1] for path in paths}


def pack_hit(expected_files: set[str], hit_paths: list[str]) -> bool:
    return bool(expected_files & basenames(hit_paths))


def pack_coverage(expected_files: set[str], hit_paths: list[str]) -> float:
    if not expected_files:
        return 0.0
    return len(expected_files & basenames(hit_paths)) / len(expected_files)


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
    return [PackCase(**row) for row in json.loads(path.read_text(encoding="utf-8"))]
