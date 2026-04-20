from __future__ import annotations

import re
from dataclasses import dataclass

CONTEXTS = ["personal", "career", "knowledge", "work", "general"]

PATH_MAP: dict[str, str] = {
    "aerus-rpg": "personal",
    "rpg-master-ai": "personal",
    "linkedin-tool": "personal",
    "avangrid": "work",
    "vault/work": "work",
    "vault/career": "career",
    "vault/knowledge": "knowledge",
    "vault/personal": "personal",
}

CONTENT_SIGNALS: dict[str, list[str]] = {
    "knowledge": [
        r"\bjava\b", r"\bspring\b", r"\bkafka\b", r"\bjvm\b",
        r"\bvirtual.?thread", r"\bspring.?boot\b", r"\bhibernate\b",
        r"\bmicroservice", r"\bdocker\b", r"\bkubernetes\b",
        r"\bfastembed\b", r"\bqdrant\b", r"\bollama\b", r"\brag\b",
        r"\bembedding\b", r"\bvector\b", r"\bllm\b",
        r"\balgorithm\b", r"\bcomplexidade\b", r"\bbig.?o\b",
        r"\bdesign.?pattern\b", r"\bsolid\b", r"\bhexagonal\b",
    ],
    "personal": [
        r"\baer[uo]s\b", r"\brpg.?master\b", r"\blinkedin.?tool\b",
        r"\bvor'athek\b", r"\bfaction\b", r"\bmutation\b",
        r"\bworld.?doc\b", r"\bbacklog\b",
    ],
    "career": [
        r"\bvaga\b", r"\bentrevista\b", r"\bsal[aá]rio\b", r"\brecruiter\b",
        r"\bcurr[íi]culo\b", r"\blinkedin\b", r"\bremote\b", r"\bjob\b",
        r"\bsenior\b.*\bposition\b", r"\bcover.?letter\b",
        r"\boffer\b", r"\bnegociar\b",
    ],
    "work": [
        r"\bavangrid\b", r"\beks\b", r"\bobservabilit\b",
        r"\bpingone\b", r"\byubico\b", r"\bestée.?lauder\b",
        r"\btcu\b", r"\bbanco.?do.?brasil\b",
    ],
    "general": [
        r"\bo que [eé]\b", r"\bwhat is\b", r"\bexplica\b", r"\bexplain\b",
        r"\bdiferen[çc]a\b", r"\bdifference\b", r"\bcomo funciona\b",
        r"\bhow does\b",
    ],
}


@dataclass
class ContextResult:
    context: str
    confidence: float
    signals: dict[str, str | None]
    display: str


class ContextDetector:
    def __init__(self, session_store) -> None:
        self._session = session_store

    def detect(self, query: str, cwd: str | None = None) -> ContextResult:
        scores: dict[str, float] = {ctx: 0.0 for ctx in CONTEXTS}

        cwd_ctx = self._score_cwd(cwd)
        if cwd_ctx:
            scores[cwd_ctx] += 0.4

        content_scores = self._score_content(query)
        for ctx, score in content_scores.items():
            scores[ctx] += score * 0.4

        # Histórico de sessão — peso 0.2
        session_ctx = self._last_context()
        if session_ctx:
            scores[session_ctx] += 0.2

        # Proteção work: nunca inferir só por histórico
        if scores["work"] <= 0.2:
            scores["work"] = 0.0

        winner = max(scores, key=scores.get)
        total = sum(scores.values()) or 1.0
        confidence = scores[winner] / total

        if confidence < 0.35:
            winner = "general"
            confidence = 0.35

        top_content = (
            max(content_scores, key=content_scores.get)
            if content_scores
            else None
        )

        return ContextResult(
            context=winner,
            confidence=confidence,
            signals={
                "cwd": cwd_ctx,
                "content": top_content,
                "session": session_ctx,
            },
            display=f"[{winner} {confidence:.0%}]",
        )

    def _score_cwd(self, cwd: str | None) -> str | None:
        if not cwd:
            return None
        path = cwd.lower()
        for pattern, ctx in PATH_MAP.items():
            if pattern in path:
                return ctx
        return None

    def _score_content(self, query: str) -> dict[str, float]:
        query_lower = query.lower()
        scores: dict[str, float] = {}
        for ctx, patterns in CONTENT_SIGNALS.items():
            hits = sum(1 for p in patterns if re.search(p, query_lower))
            if hits > 0:
                scores[ctx] = min(hits / 3, 1.0)
        return scores

    def _last_context(self) -> str | None:
        # SessionStore é async; aqui retornamos None sincronamente
        # O contexto de sessão é injetado via estado externo quando necessário
        return None
