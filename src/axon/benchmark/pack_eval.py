"""Scoring for pack quality: did the pack reach the files the work touched?

Two numbers, both over basenames so a moved file still matches: whether any
expected file appears at all, and what fraction of them did. Kept free of I/O
so the scoring is testable without a database.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
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


def load_cases(path: Path) -> list[PackCase]:
    return [PackCase(**row) for row in json.loads(path.read_text(encoding="utf-8"))]
