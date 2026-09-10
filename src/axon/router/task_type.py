from __future__ import annotations

from enum import StrEnum


class TaskType(StrEnum):
    TRIVIAL_COMPLETION = "TRIVIAL_COMPLETION"
    CODE_ANALYSIS = "CODE_ANALYSIS"
    ARCHITECTURE = "ARCHITECTURE"
    DEEP_REASONING = "DEEP_REASONING"
    LOCAL_ONLY = "LOCAL_ONLY"
    UNKNOWN = "UNKNOWN"
