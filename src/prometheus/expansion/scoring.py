from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum

import ollama

from prometheus.config.runtime import load_runtime_config

DEFAULT_LOCAL_MODEL = "gemma4:e4b"
_RUNTIME = load_runtime_config()
_SCORING_PROMPT = """
Voce classifica candidatos de expansao de conhecimento.

Regras obrigatorias:
- Use somente o texto extraido fornecido.
- Nao invente fatos, datas, comandos ou conclusoes fora do texto.
- Responda somente em JSON.
- Scores devem ficar entre 0.0 e 1.0.
- evidence_quotes deve conter apenas trechos literais do texto extraido.

Formato esperado:
{
  "relevance": 0.0,
  "novelty": 0.0,
  "actionability": 0.0,
  "evidence": 0.0,
  "decision": "keep|maybe|discard",
  "reasoning": "resumo curto em portugues",
  "evidence_quotes": ["trecho literal 1", "trecho literal 2"]
}
"""


class ExpansionDecision(str, Enum):
    KEEP = "keep"
    MAYBE = "maybe"
    DISCARD = "discard"


@dataclass(frozen=True)
class ExpansionCandidate:
    title: str
    extracted_text: str
    source_url: str
    published_at: str | None = None


@dataclass(frozen=True)
class ExpansionScore:
    relevance: float
    novelty: float
    actionability: float
    evidence: float

    @property
    def weighted_total(self) -> float:
        return round(
            (self.relevance * 0.35)
            + (self.novelty * 0.20)
            + (self.actionability * 0.25)
            + (self.evidence * 0.20),
            4,
        )


@dataclass(frozen=True)
class ExpansionScoreResult:
    candidate: ExpansionCandidate
    topic: str
    score: ExpansionScore
    decision: ExpansionDecision
    reasoning: str
    evidence_quotes: tuple[str, ...]
    source: str
    model: str


def score_candidate(
    candidate: ExpansionCandidate,
    topic: str,
    *,
    model: str = DEFAULT_LOCAL_MODEL,
    host: str | None = None,
) -> ExpansionScoreResult:
    normalized = ExpansionCandidate(
        title=candidate.title.strip(),
        extracted_text=_normalize_text(candidate.extracted_text),
        source_url=candidate.source_url.strip(),
        published_at=(candidate.published_at or None),
    )
    heuristic = _heuristic_result(normalized, topic)

    if not normalized.extracted_text:
        return heuristic

    try:
        local_result = _score_with_local_slm(normalized, topic, model=model, host=host)
        if local_result is not None:
            return local_result
    except Exception:
        pass

    return heuristic


def score_candidates(
    candidates: list[ExpansionCandidate],
    topic: str,
    *,
    model: str = DEFAULT_LOCAL_MODEL,
    host: str | None = None,
) -> list[ExpansionScoreResult]:
    return [
        score_candidate(candidate, topic, model=model, host=host)
        for candidate in candidates
    ]


def _score_with_local_slm(
    candidate: ExpansionCandidate,
    topic: str,
    *,
    model: str,
    host: str | None,
) -> ExpansionScoreResult | None:
    client = ollama.Client(host=host or _RUNTIME.ollama_local_host)
    response = client.chat(
        model=model,
        format="json",
        messages=[
            {"role": "system", "content": _SCORING_PROMPT},
            {"role": "user", "content": _build_scoring_input(candidate, topic)},
        ],
        options={"temperature": 0},
    )
    raw = response["message"]["content"]
    payload = _parse_score_payload(raw)
    if payload is None:
        return None

    evidence_quotes = _validated_quotes(
        payload.get("evidence_quotes", []),
        candidate.extracted_text,
    )
    reasoning = str(payload.get("reasoning", "")).strip()
    if not evidence_quotes:
        return None

    score = ExpansionScore(
        relevance=_clamp_score(payload.get("relevance")),
        novelty=_clamp_score(payload.get("novelty")),
        actionability=_clamp_score(payload.get("actionability")),
        evidence=_clamp_score(payload.get("evidence")),
    )
    decision = _decision_from_scores(score)
    if reasoning:
        reasoning = _normalize_reasoning(reasoning, evidence_quotes)
    else:
        reasoning = _heuristic_reasoning(score, evidence_quotes)

    return ExpansionScoreResult(
        candidate=candidate,
        topic=topic.strip(),
        score=score,
        decision=decision,
        reasoning=reasoning,
        evidence_quotes=evidence_quotes,
        source="local_slm",
        model=model,
    )


def _heuristic_result(candidate: ExpansionCandidate, topic: str) -> ExpansionScoreResult:
    score = ExpansionScore(
        relevance=_score_relevance(topic, candidate),
        novelty=_score_novelty(candidate),
        actionability=_score_actionability(candidate.extracted_text),
        evidence=_score_evidence(candidate),
    )
    evidence_quotes = _heuristic_evidence_quotes(candidate.extracted_text)
    return ExpansionScoreResult(
        candidate=candidate,
        topic=topic.strip(),
        score=score,
        decision=_decision_from_scores(score),
        reasoning=_heuristic_reasoning(score, evidence_quotes),
        evidence_quotes=evidence_quotes,
        source="heuristic",
        model="heuristic",
    )


def _build_scoring_input(candidate: ExpansionCandidate, topic: str) -> str:
    published_at = candidate.published_at or "(unknown)"
    return (
        f"TOPIC:\n{topic.strip()}\n\n"
        f"TITLE:\n{candidate.title}\n\n"
        f"SOURCE_URL:\n{candidate.source_url}\n\n"
        f"PUBLISHED_AT:\n{published_at}\n\n"
        f"EXTRACTED_TEXT:\n{candidate.extracted_text}"
    )


def _parse_score_payload(raw: str) -> dict[str, object] | None:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start == -1 or end == 0:
            return None
        try:
            data = json.loads(raw[start:end])
        except json.JSONDecodeError:
            return None
    return data if isinstance(data, dict) else None


def _validated_quotes(quotes: object, extracted_text: str) -> tuple[str, ...]:
    if not isinstance(quotes, list):
        return ()
    valid: list[str] = []
    for quote in quotes:
        if not isinstance(quote, str):
            continue
        normalized_quote = _normalize_text(quote)
        if len(normalized_quote) < 12:
            continue
        if normalized_quote in extracted_text and normalized_quote not in valid:
            valid.append(normalized_quote)
    return tuple(valid[:3])


def _normalize_reasoning(reasoning: str, quotes: tuple[str, ...]) -> str:
    compact = " ".join(reasoning.split())
    if not compact:
        return _heuristic_reasoning(
            ExpansionScore(0.0, 0.0, 0.0, 0.0),
            quotes,
        )
    if quotes:
        return f"{compact} Evidencia: {quotes[0]}"
    return compact


def _heuristic_reasoning(score: ExpansionScore, quotes: tuple[str, ...]) -> str:
    parts = [
        f"relevance={score.relevance:.2f}",
        f"novelty={score.novelty:.2f}",
        f"actionability={score.actionability:.2f}",
        f"evidence={score.evidence:.2f}",
    ]
    if quotes:
        parts.append(f"evidencia=\"{quotes[0]}\"")
    return ", ".join(parts)


def _decision_from_scores(score: ExpansionScore) -> ExpansionDecision:
    total = score.weighted_total
    if score.relevance < 0.2 or score.evidence < 0.2:
        return ExpansionDecision.DISCARD
    if total >= 0.72 and score.relevance >= 0.55 and score.evidence >= 0.45:
        return ExpansionDecision.KEEP
    if total >= 0.38:
        return ExpansionDecision.MAYBE
    return ExpansionDecision.DISCARD


def _score_relevance(topic: str, candidate: ExpansionCandidate) -> float:
    topic_tokens = _tokenize(topic)
    if not topic_tokens:
        return 0.0
    haystack = _tokenize(f"{candidate.title} {candidate.extracted_text}")
    overlap = len(topic_tokens & haystack) / len(topic_tokens)
    phrase_bonus = (
        0.2 if topic.strip().lower() in candidate.extracted_text.lower() else 0.0
    )
    title_bonus = 0.15 if topic_tokens & _tokenize(candidate.title) else 0.0
    return _bound(overlap + phrase_bonus + title_bonus)


def _score_novelty(candidate: ExpansionCandidate) -> float:
    text = candidate.extracted_text.lower()
    indicators = 0
    patterns = [
        r"\bnew\b",
        r"\bintroduced\b",
        r"\bchanged\b",
        r"\bupdate(d)?\b",
        r"\brelease(d)?\b",
        r"\bdeprecated\b",
        r"\bmigration\b",
        r"\bversion\b",
    ]
    for pattern in patterns:
        if re.search(pattern, text):
            indicators += 1
    if candidate.published_at:
        indicators += 1
    if re.search(r"\b20\d{2}\b", text):
        indicators += 1
    density = min(len(_tokenize(candidate.extracted_text)) / 120, 1.0) * 0.2
    return _bound((indicators * 0.14) + density)


def _score_actionability(text: str) -> float:
    lowered = text.lower()
    indicators = [
        r"```",
        r"\bexample\b",
        r"\bhow to\b",
        r"\bstep\b",
        r"\bconfigure\b",
        r"\binstall\b",
        r"\brun\b",
        r"\buse\b",
        r"\bcommand\b",
        r"\bfix\b",
    ]
    hits = sum(1 for pattern in indicators if re.search(pattern, lowered))
    if re.search(r"^\s*[-*]\s+", text, flags=re.MULTILINE):
        hits += 1
    if re.search(r"^\s*\d+\.\s+", text, flags=re.MULTILINE):
        hits += 1
    return _bound((hits / 7) + min(text.count("\n") / 20, 0.15))


def _score_evidence(candidate: ExpansionCandidate) -> float:
    text = candidate.extracted_text
    points = 0.0
    if candidate.source_url:
        points += 0.2
    if candidate.published_at:
        points += 0.1
    if re.search(r"```", text):
        points += 0.25
    if re.search(r"\b[A-Z][a-z]+[A-Z][A-Za-z]+\b", text):
        points += 0.1
    if re.search(r"\b\d+(\.\d+)?\b", text):
        points += 0.15
    if len(_heuristic_evidence_quotes(text)) >= 2:
        points += 0.2
    return _bound(points)


def _heuristic_evidence_quotes(text: str) -> tuple[str, ...]:
    lines = [line.strip() for line in text.splitlines() if len(line.strip()) >= 18]
    prioritized: list[str] = []
    for line in lines:
        if "```" in line:
            continue
        if re.search(
            r"\b(error|warning|example|command|config|version|release|deprecated)\b",
            line,
            re.I,
        ):
            prioritized.append(line)
    if not prioritized:
        prioritized = lines
    return tuple(prioritized[:2])


def _normalize_text(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.strip().splitlines()).strip()


def _tokenize(text: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]{3,}", text.lower())}


def _clamp_score(value: object) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0.0
    return _bound(numeric)


def _bound(value: float) -> float:
    return round(min(max(value, 0.0), 1.0), 4)
