"""Model-comparison benchmark for the local Ollama roles (scoring, compressor).

Runs candidate models over real task cases and emits objective ``BenchmarkResult``
checks (JSON validity, quote grounding, decision match, symbol preservation) plus
latency. The model call is injected (``chat`` / ``compress``) so the check logic is
testable without a GPU; the real runner wires it to an Ollama endpoint.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from time import perf_counter

from axon.benchmark.contracts import BenchmarkCheck, BenchmarkResult
from axon.expansion.scoring import (
    ExpansionCandidate,
    ExpansionDecision,
    ExpansionScore,
    _build_scoring_input,
    _clamp_score,
    _decision_from_scores,
    _parse_score_payload,
    _validated_quotes,
)

ChatFn = Callable[[str, str], str]
CompressFn = Callable[[str, str], str]

# These roles process a few short paragraphs. The desktop's default context is huge
# (qwen3:4b advertises 262144), which blows the KV cache to multi-GB — OOM on small
# models, 150s latency on qwen3. Pin a small ctx; the tasks never need more.
EVAL_NUM_CTX = 8192


def make_ollama_scoring_chat(host: str) -> ChatFn:
    """Real scoring backend: same system prompt + JSON mode as production."""
    import ollama

    from axon.expansion.scoring import _SCORING_PROMPT

    client = ollama.Client(host=host)
    # /no_think keeps reasoning models (Qwen3) out of thinking mode: these roles are
    # high-frequency JSON tasks, not chain-of-thought; thinking blows up latency.
    system = "/no_think\n" + _SCORING_PROMPT

    def chat(model: str, prompt: str) -> str:
        response = client.chat(
            model=model,
            format="json",
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            options={"temperature": 0, "num_ctx": EVAL_NUM_CTX},
        )
        return response["message"]["content"]

    return chat


def make_ollama_compress(host: str) -> CompressFn:
    """Real compressor backend: same caveman system prompt as production."""
    import ollama

    from axon.router.compressor import _SYSTEM_PROMPT

    client = ollama.Client(host=host)
    system = "/no_think\n" + _SYSTEM_PROMPT

    def compress(model: str, text: str) -> str:
        response = client.chat(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": text},
            ],
            options={"temperature": 0, "num_ctx": EVAL_NUM_CTX},
        )
        return response["message"]["content"]

    return compress


def make_litellm_scoring_chat() -> ChatFn:
    """Cloud scoring backend via litellm (groq/cerebras/openrouter/...).

    The ``model`` passed to evaluate_scoring_model is the full litellm id, e.g.
    ``groq/openai/gpt-oss-120b`` or ``cerebras/gpt-oss-120b``. Same system prompt
    + JSON mode as production.
    """
    import litellm

    from axon.expansion.scoring import _SCORING_PROMPT

    def chat(model: str, prompt: str) -> str:
        response = litellm.completion(
            model=model,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _SCORING_PROMPT},
                {"role": "user", "content": prompt},
            ],
        )
        return response.choices[0].message.content

    return chat


def make_litellm_compress() -> CompressFn:
    """Cloud compressor backend via litellm. ``model`` is the full litellm id."""
    import litellm

    from axon.router.compressor import _SYSTEM_PROMPT

    def compress(model: str, text: str) -> str:
        response = litellm.completion(
            model=model,
            temperature=0,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
        )
        return response.choices[0].message.content

    return compress


def make_litellm_adr_chat(max_tokens: int = 400) -> ChatFn:
    """Cloud ADR-classifier backend via litellm.

    Mirrors ``axon.adr.inference._call_llm`` exactly: the prompt arrives
    fully rendered, sent as a single user message with ``max_tokens=400``
    and no temperature override — the eval must see what production sees.
    ``max_tokens`` is overridable to measure candidate production contracts
    (reasoning models truncate at 400).
    """
    import litellm

    def chat(model: str, prompt: str) -> str:
        response = litellm.completion(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
        )
        return (response.choices[0].message.content or "").strip()

    return chat


@dataclass(frozen=True)
class ADREvalCase:
    commit_message: str
    diff_summary: str
    expected: str  # "adr" | "null"
    key_terms: tuple[str, ...]


def _adr_prompt(case: ADREvalCase, template: str | None = None) -> str:
    if template is None:
        from axon.adr.inference import _TEMPLATE_PATH

        template = _TEMPLATE_PATH.read_text(encoding="utf-8")
    return template.format(
        commit_message=case.commit_message, diff_summary=case.diff_summary
    )


def _parse_adr_reply(raw: str) -> dict | None:
    """Strict production parse (``axon.adr.inference``): plain JSON only."""
    import json

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def evaluate_adr_model(
    model: str,
    cases: Sequence[ADREvalCase],
    *,
    chat: ChatFn,
    template: str | None = None,
) -> BenchmarkResult:
    checks: list[BenchmarkCheck] = []
    start = perf_counter()
    for case in cases:
        raw = chat(model, _adr_prompt(case, template))
        is_null = not raw or raw.lower().startswith("null")
        payload = None if is_null else _parse_adr_reply(raw)

        if case.expected == "null":
            checks.append(
                BenchmarkCheck(
                    name="verdict_match",
                    passed=is_null,
                    expected="null",
                    actual="null" if is_null else raw[:60],
                )
            )
            continue

        checks.append(
            BenchmarkCheck(
                name="verdict_match",
                passed=payload is not None,
                expected="ADR JSON",
                actual="parsed" if payload is not None else raw[:60],
            )
        )
        checks.append(
            BenchmarkCheck(
                name="json_valid",
                passed=payload is not None,
                expected="parseable JSON object",
                actual="parsed" if payload is not None else "unparseable",
            )
        )
        text = " ".join(
            str(payload.get(k, ""))
            for k in ("title", "context", "decision", "rationale")
        ).lower() if payload else ""
        missing = [t for t in case.key_terms if t.lower() not in text]
        checks.append(
            BenchmarkCheck(
                name="key_terms_present",
                passed=not missing,
                expected=f"mentions {list(case.key_terms)}",
                actual=f"missing {missing}" if missing else "all present",
            )
        )
    duration_ms = (perf_counter() - start) * 1000
    return BenchmarkResult(
        suite="model_eval.adr",
        name=model,
        duration_ms=duration_ms,
        checks=tuple(checks),
    )


@dataclass(frozen=True)
class ScoringEvalCase:
    candidate: ExpansionCandidate
    topic: str
    gold_decision: ExpansionDecision


@dataclass(frozen=True)
class CompressorEvalCase:
    text: str
    required_symbols: tuple[str, ...]


def evaluate_scoring_model(
    model: str,
    cases: Sequence[ScoringEvalCase],
    *,
    chat: ChatFn,
) -> BenchmarkResult:
    checks: list[BenchmarkCheck] = []
    start = perf_counter()
    for case in cases:
        raw = chat(model, _build_scoring_input(case.candidate, case.topic))
        payload = _parse_score_payload(raw)
        checks.append(
            BenchmarkCheck(
                name="json_valid",
                passed=payload is not None,
                expected="parseable JSON object",
                actual="parsed" if payload is not None else "unparseable",
            )
        )

        quotes = payload.get("evidence_quotes", []) if payload else []
        cited = (
            [q for q in quotes if isinstance(q, str) and q.strip()]
            if isinstance(quotes, list)
            else []
        )
        validated = _validated_quotes(quotes, case.candidate.extracted_text)
        grounded = bool(cited) and len(validated) == len(cited)
        checks.append(
            BenchmarkCheck(
                name="grounded",
                passed=grounded,
                expected="every evidence_quote is literal text",
                actual=f"{len(validated)}/{len(cited)} quotes literal",
            )
        )

        if payload is not None:
            score = ExpansionScore(
                relevance=_clamp_score(payload.get("relevance")),
                novelty=_clamp_score(payload.get("novelty")),
                actionability=_clamp_score(payload.get("actionability")),
                evidence=_clamp_score(payload.get("evidence")),
            )
            decision: ExpansionDecision | None = _decision_from_scores(score)
        else:
            decision = None
        checks.append(
            BenchmarkCheck(
                name="decision_match",
                passed=decision == case.gold_decision,
                expected=case.gold_decision.value,
                actual=decision.value if decision is not None else "none",
            )
        )
    duration_ms = (perf_counter() - start) * 1000
    return BenchmarkResult(
        suite="model_eval.scoring",
        name=model,
        duration_ms=duration_ms,
        checks=tuple(checks),
    )


def evaluate_compressor_model(
    model: str,
    cases: Sequence[CompressorEvalCase],
    *,
    compress: CompressFn,
) -> BenchmarkResult:
    checks: list[BenchmarkCheck] = []
    start = perf_counter()
    for case in cases:
        compressed = compress(model, case.text)
        missing = [symbol for symbol in case.required_symbols if symbol not in compressed]
        checks.append(
            BenchmarkCheck(
                name="symbols_preserved",
                passed=not missing,
                expected="all required symbols kept",
                actual=f"missing {missing}" if missing else "all kept",
            )
        )

        words_in = len(case.text.split()) or 1
        ratio = len(compressed.split()) / words_in
        checks.append(
            BenchmarkCheck(
                name="compressed",
                passed=ratio < 1.0,
                expected="ratio < 1.0",
                actual=f"{ratio:.2f}",
            )
        )
    duration_ms = (perf_counter() - start) * 1000
    return BenchmarkResult(
        suite="model_eval.compressor",
        name=model,
        duration_ms=duration_ms,
        checks=tuple(checks),
    )
