import re
from dataclasses import dataclass

SIGNALS: dict[str, list[str]] = {
    "knowledge": [r"\bjava\b", r"\bspring\b", r"\bpython\b"],
    "personal":  [r"\baer[uo]s\b", r"\brpg\b"],
    "career":    [r"\bvaga\b", r"\bentrevista\b"],
    "work":      [r"\bavangrid\b", r"\beks\b"],
}


@dataclass
class DetectionResult:
    context: str
    confidence: float


def detect(query: str, cwd: str | None = None) -> DetectionResult:
    scores: dict[str, float] = {k: 0.0 for k in SIGNALS}

    for ctx, patterns in SIGNALS.items():
        hits = sum(1 for p in patterns if re.search(p, query.lower()))
        if hits:
            scores[ctx] = min(hits / 3, 1.0)

    if not any(scores.values()):
        return DetectionResult("general", 0.5)

    winner = max(scores, key=scores.get)
    total = sum(scores.values()) or 1.0
    return DetectionResult(winner, scores[winner] / total)


def is_work_safe(result: DetectionResult, explicit: bool = False) -> bool:
    if result.context == "work" and not explicit:
        return False
    return True
