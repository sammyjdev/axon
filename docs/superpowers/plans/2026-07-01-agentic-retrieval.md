# Self-correcting retrieval Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a bounded corrective-RAG loop between retrieval and compression in `ask()`, so it grades its own retrieval and re-tries once before answering.

**Architecture:** A new pure module `src/axon/retrieval/self_correct.py` grades hits via a hybrid cascade (score bands + LLM judge in the gray zone) and, if insufficient, runs one retry (query reformulation or graph fallback, chosen by query shape), then gives up honestly. `ask()` wires concrete judge/reformulate/graph callables into the pure `correct_retrieval` and gates the whole thing behind `AXON_SELF_CORRECT`. A new `src/axon/benchmark/retrieval_eval.py` measures recall delta over a golden set.

**Tech Stack:** Python 3.11+, asyncio, litellm (via `axon.router.llm_backend.litellm_kwargs`), pgvector store, pytest.

## Global Constraints

- Python 3.11+ with type hints. Domain/value objects use `@dataclass(frozen=True)` (dec-105 reserves Pydantic for persisted models; these are in-process).
- TDD: no production code before a failing test (CLAUDE.md Agent Rules).
- Prefer async for I/O paths; `SessionStore` must be `.init()`-ed before use.
- Never switch `ctx` during correction (keeps dec-109 restricted-context gate intact).
- Judge/reformulate use the FREE-profile trivial model `_bottom_tier_model()` from `axon.router.engine`, called through `litellm_kwargs(...)`, `temperature=0`, `response_format={"type": "json_object"}`.
- Validate with `rtk pytest tests/ -q`, `rtk ruff check`.
- `hit` shape (from `pg_vector_store.search`): `{"score": float, "id": ..., "payload": {"symbol": str, "content": str, "file_path": str, ...}}`. `score` is cosine similarity (higher = better).

---

### Task 1: Cascade helpers (score aggregation, structural classifier, sufficiency)

**Files:**
- Create: `src/axon/retrieval/__init__.py`
- Create: `src/axon/retrieval/self_correct.py`
- Test: `tests/retrieval/test_self_correct_cascade.py`

**Interfaces:**
- Produces:
  - `LOW: float = 0.35`, `HIGH: float = 0.65` (module constants, calibrated in Task 5)
  - `aggregate_score(hits: list[dict]) -> float`
  - `is_structural(query: str) -> bool`
  - `grade(hits: list[dict], query: str, code_context: str, judge_fn: Callable[[str, str], bool]) -> tuple[bool, str]` — returns `(sufficient, verdict_label)`; `judge_fn` is only called in the gray zone.

- [ ] **Step 1: Write the failing test**

```python
# tests/retrieval/test_self_correct_cascade.py
from axon.retrieval.self_correct import aggregate_score, is_structural, grade


def test_aggregate_score_is_best_hit():
    hits = [{"score": 0.4}, {"score": 0.9}, {"score": 0.1}]
    assert aggregate_score(hits) == 0.9


def test_aggregate_score_empty_is_zero():
    assert aggregate_score([]) == 0.0


def test_is_structural_true_on_dependency_phrasing():
    assert is_structural("quem usa PolicyRegistry?")
    assert is_structural("what depends on AuthService")


def test_is_structural_true_on_symbol_token():
    assert is_structural("ContextDetector.detect flow")


def test_is_structural_false_on_prose_query():
    assert not is_structural("como funciona a compressao de contexto")


def test_grade_empty_hits_insufficient_without_judge():
    called = []
    verdict = grade([], "q", "", lambda q, c: called.append(1) or True)
    assert verdict == (False, "empty")
    assert called == []


def test_grade_low_score_insufficient_without_judge():
    verdict = grade([{"score": 0.10}], "q", "ctx", lambda q, c: True)
    assert verdict == (False, "low_score")


def test_grade_high_score_sufficient_without_judge():
    called = []
    verdict = grade([{"score": 0.90}], "q", "ctx", lambda q, c: called.append(1) or False)
    assert verdict == (True, "high_score")
    assert called == []


def test_grade_gray_zone_defers_to_judge():
    assert grade([{"score": 0.50}], "q", "ctx", lambda q, c: True) == (True, "judge_sufficient")
    assert grade([{"score": 0.50}], "q", "ctx", lambda q, c: False) == (False, "judge_insufficient")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `rtk pytest tests/retrieval/test_self_correct_cascade.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'axon.retrieval'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/axon/retrieval/__init__.py
```
(empty file)

```python
# src/axon/retrieval/self_correct.py
"""Self-correcting retrieval: grade the retrieval, re-try once, or give up
honestly. Pure orchestration — LLM and graph access are injected callables so
the loop is testable without a live model or the MCP server.

See docs/superpowers/specs/2026-07-01-agentic-retrieval-design.md.
"""
from __future__ import annotations

import re
from typing import Callable

# Calibrated against the golden set (see retrieval_eval). Below LOW: retry
# without asking the judge. At/above HIGH: trust the retrieval. Gray zone in
# between: ask the judge. Similarity is a weak relevance proxy, so the gray zone
# is deliberately wide.
LOW: float = 0.35
HIGH: float = 0.65

_STRUCTURAL_PHRASES = (
    "depende", "dependencia", "quem usa", "quem chama", "quem importa",
    "depends on", "who uses", "who calls", "call graph", "callers of",
    "imported by", "importado", "grafo",
)
# CamelCase (AuthService), dotted access (module.attr), or call syntax (fn()).
_SYMBOL_RE = re.compile(r"[A-Z][a-z]+[A-Z]|\b\w+\.\w+\b|\b\w+\(\)")


def aggregate_score(hits: list[dict]) -> float:
    """Confidence of the retrieval = the best single hit's cosine score."""
    return max((float(h.get("score", 0.0)) for h in hits), default=0.0)


def is_structural(query: str) -> bool:
    """True when the query is about code structure/dependencies, where the
    graph fallback beats vector search."""
    q = query.lower()
    if any(phrase in q for phrase in _STRUCTURAL_PHRASES):
        return True
    return bool(_SYMBOL_RE.search(query))


def grade(
    hits: list[dict],
    query: str,
    code_context: str,
    judge_fn: Callable[[str, str], bool],
) -> tuple[bool, str]:
    """Hybrid cascade. Returns (sufficient, verdict_label). judge_fn is called
    ONLY in the gray zone [LOW, HIGH)."""
    if not hits:
        return False, "empty"
    score = aggregate_score(hits)
    if score < LOW:
        return False, "low_score"
    if score >= HIGH:
        return True, "high_score"
    verdict = judge_fn(query, code_context)
    return (verdict, "judge_sufficient" if verdict else "judge_insufficient")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `rtk pytest tests/retrieval/test_self_correct_cascade.py -v`
Expected: PASS (9 passed)

- [ ] **Step 5: Commit**

```bash
git add src/axon/retrieval/__init__.py src/axon/retrieval/self_correct.py tests/retrieval/test_self_correct_cascade.py
git commit -m "feat(retrieval): cascade helpers for self-correcting retrieval"
```

---

### Task 2: `correct_retrieval` orchestration

**Files:**
- Modify: `src/axon/retrieval/self_correct.py`
- Test: `tests/retrieval/test_correct_retrieval.py`

**Interfaces:**
- Consumes: `grade`, `is_structural` (Task 1).
- Produces:
  - `@dataclass(frozen=True) class CorrectionResult: code_context: str; pack: object; hits: list[dict]; meta: dict`
  - `async def correct_retrieval(query, ctx, code_context, pack, hits, *, retrieve_fn, judge_fn, reformulate_fn, graph_fn, enabled=True) -> CorrectionResult`
    - `retrieve_fn: Callable[[str], Awaitable[tuple[str, object, list[dict]]]]` — re-runs retrieval for a (reformulated) query, returns `(code_context, pack, hits)`.
    - `judge_fn: Callable[[str, str], bool]`.
    - `reformulate_fn: Callable[[str], str]`.
    - `graph_fn: Callable[[list[dict]], Awaitable[str]]` — returns graph-neighbor context text, or `""` when nothing found.
  - `meta` keys: `verdict: str`, `strategy_used: str | None`, `retried: bool`, `gave_up: bool`.

- [ ] **Step 1: Write the failing test**

```python
# tests/retrieval/test_correct_retrieval.py
import pytest
from axon.retrieval.self_correct import correct_retrieval, CorrectionResult

PACK = object()


async def _retrieve_ok(q):
    return ("REFORMULATED CONTEXT", PACK, [{"score": 0.95, "payload": {"symbol": "X"}}])


async def _retrieve_still_bad(q):
    return ("STILL BAD", PACK, [{"score": 0.05, "payload": {"symbol": "X"}}])


async def _graph_hit(hits):
    return "## Dependencias\nA -> B"


async def _graph_empty(hits):
    return ""


@pytest.mark.asyncio
async def test_disabled_returns_input_untouched():
    r = await correct_retrieval(
        "q", "personal", "CTX", PACK, [{"score": 0.05}],
        retrieve_fn=_retrieve_still_bad, judge_fn=lambda q, c: False,
        reformulate_fn=lambda q: q, graph_fn=_graph_empty, enabled=False,
    )
    assert r.code_context == "CTX"
    assert r.meta["retried"] is False and r.meta["gave_up"] is False


@pytest.mark.asyncio
async def test_sufficient_first_pass_no_retry():
    r = await correct_retrieval(
        "como funciona X", "personal", "CTX", PACK, [{"score": 0.90}],
        retrieve_fn=_retrieve_still_bad, judge_fn=lambda q, c: False,
        reformulate_fn=lambda q: "SHOULD NOT BE CALLED", graph_fn=_graph_empty,
    )
    assert r.meta == {"verdict": "high_score", "strategy_used": None, "retried": False, "gave_up": False}
    assert r.code_context == "CTX"


@pytest.mark.asyncio
async def test_reformulate_path_recovers():
    r = await correct_retrieval(
        "como funciona a compressao", "personal", "BAD", PACK, [{"score": 0.05}],
        retrieve_fn=_retrieve_ok, judge_fn=lambda q, c: False,
        reformulate_fn=lambda q: q + " detalhado", graph_fn=_graph_empty,
    )
    assert r.code_context == "REFORMULATED CONTEXT"
    assert r.meta["strategy_used"] == "reformulate"
    assert r.meta["retried"] is True and r.meta["gave_up"] is False


@pytest.mark.asyncio
async def test_structural_query_uses_graph():
    r = await correct_retrieval(
        "quem usa AuthService", "personal", "BAD", PACK, [{"score": 0.05, "payload": {"symbol": "AuthService"}}],
        retrieve_fn=_retrieve_ok, judge_fn=lambda q, c: False,
        reformulate_fn=lambda q: "SHOULD NOT BE CALLED", graph_fn=_graph_hit,
    )
    assert "A -> B" in r.code_context and "BAD" in r.code_context
    assert r.meta["strategy_used"] == "graph"
    assert r.meta["gave_up"] is False


@pytest.mark.asyncio
async def test_reformulate_fails_gives_up_with_header():
    r = await correct_retrieval(
        "como funciona a compressao", "personal", "BAD", PACK, [{"score": 0.05}],
        retrieve_fn=_retrieve_still_bad, judge_fn=lambda q, c: False,
        reformulate_fn=lambda q: q, graph_fn=_graph_empty,
    )
    assert r.code_context.startswith("⚠ contexto recuperado pode ser insuficiente")
    assert r.meta["gave_up"] is True and r.meta["strategy_used"] == "reformulate"


@pytest.mark.asyncio
async def test_structural_empty_graph_gives_up():
    r = await correct_retrieval(
        "quem usa AuthService", "personal", "BAD", PACK, [{"score": 0.05, "payload": {"symbol": "AuthService"}}],
        retrieve_fn=_retrieve_ok, judge_fn=lambda q, c: False,
        reformulate_fn=lambda q: q, graph_fn=_graph_empty,
    )
    assert r.code_context.startswith("⚠ contexto recuperado")
    assert r.meta["gave_up"] is True and r.meta["strategy_used"] == "graph"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `rtk pytest tests/retrieval/test_correct_retrieval.py -v`
Expected: FAIL with `ImportError: cannot import name 'correct_retrieval'`

- [ ] **Step 3: Write minimal implementation** (append to `src/axon/retrieval/self_correct.py`)

```python
from dataclasses import dataclass
from typing import Awaitable

_GIVE_UP_HEADER = "⚠ contexto recuperado pode ser insuficiente para esta query"


@dataclass(frozen=True)
class CorrectionResult:
    code_context: str
    pack: object
    hits: list[dict]
    meta: dict


async def correct_retrieval(
    query: str,
    ctx: str | None,
    code_context: str,
    pack: object,
    hits: list[dict],
    *,
    retrieve_fn: "Callable[[str], Awaitable[tuple[str, object, list[dict]]]]",
    judge_fn: Callable[[str, str], bool],
    reformulate_fn: Callable[[str], str],
    graph_fn: "Callable[[list[dict]], Awaitable[str]]",
    enabled: bool = True,
) -> CorrectionResult:
    """Grade the retrieval; on insufficiency run exactly one recovery step
    (graph fallback for structural queries, else query reformulation); if still
    insufficient, return the original context with an honest give-up header."""
    if not enabled:
        return CorrectionResult(code_context, pack, hits,
                                {"verdict": "disabled", "strategy_used": None,
                                 "retried": False, "gave_up": False})

    sufficient, verdict = grade(hits, query, code_context, judge_fn)
    if sufficient:
        return CorrectionResult(code_context, pack, hits,
                                {"verdict": verdict, "strategy_used": None,
                                 "retried": False, "gave_up": False})

    if is_structural(query):
        strategy = "graph"
        graph_ctx = await graph_fn(hits)
        if graph_ctx:
            return CorrectionResult(f"{code_context}\n\n{graph_ctx}", pack, hits,
                                    {"verdict": verdict, "strategy_used": "graph",
                                     "retried": True, "gave_up": False})
    else:
        strategy = "reformulate"
        new_query = reformulate_fn(query)
        code_context2, pack2, hits2 = await retrieve_fn(new_query)
        sufficient2, verdict2 = grade(hits2, new_query, code_context2, judge_fn)
        if sufficient2:
            return CorrectionResult(code_context2, pack2, hits2,
                                    {"verdict": verdict2, "strategy_used": "reformulate",
                                     "retried": True, "gave_up": False})

    return CorrectionResult(f"{_GIVE_UP_HEADER}\n\n{code_context}", pack, hits,
                            {"verdict": verdict, "strategy_used": strategy,
                             "retried": True, "gave_up": True})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `rtk pytest tests/retrieval/test_correct_retrieval.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add src/axon/retrieval/self_correct.py tests/retrieval/test_correct_retrieval.py
git commit -m "feat(retrieval): correct_retrieval one-retry orchestration"
```

---

### Task 3: Wire into `ask()` with kill-switch and trace stage

**Files:**
- Modify: `src/axon/mcp/server.py` (add helpers near other private helpers ~line 300; call inside `ask()` at ~line 594, after `_retrieve_context`, before the compression block at ~line 606)
- Test: `tests/mcp/test_ask_self_correct.py`

**Interfaces:**
- Consumes: `correct_retrieval` (Task 2), `_retrieve_context`, `_get_session_store`, `_RUNTIME`, `litellm_kwargs`, `_bottom_tier_model`.
- Produces (module-level in `server.py`):
  - `def _self_correct_enabled() -> bool` — reads `AXON_SELF_CORRECT` (default on).
  - `def _judge_sufficiency(query: str, context: str) -> bool`
  - `def _reformulate_query(query: str) -> str`
  - `async def _graph_fallback(hits: list[dict]) -> str`

- [ ] **Step 1: Write the failing test**

```python
# tests/mcp/test_ask_self_correct.py
import axon.mcp.server as server


def test_self_correct_enabled_default_on(monkeypatch):
    monkeypatch.delenv("AXON_SELF_CORRECT", raising=False)
    assert server._self_correct_enabled() is True


def test_self_correct_kill_switch_off(monkeypatch):
    monkeypatch.setenv("AXON_SELF_CORRECT", "0")
    assert server._self_correct_enabled() is False


def test_judge_sufficiency_parses_true(monkeypatch):
    class _Msg:
        content = '{"sufficient": true}'
    class _Choice:
        message = _Msg()
    class _Resp:
        choices = [_Choice()]
    monkeypatch.setattr(server.litellm, "completion", lambda **kw: _Resp())
    assert server._judge_sufficiency("q", "ctx") is True


def test_judge_sufficiency_false_on_malformed(monkeypatch):
    class _Msg:
        content = "not json"
    class _Choice:
        message = _Msg()
    class _Resp:
        choices = [_Choice()]
    monkeypatch.setattr(server.litellm, "completion", lambda **kw: _Resp())
    # Malformed judge output must not crash ask(); default to insufficient
    # (conservative: prefer a retry over trusting an unparseable verdict).
    assert server._judge_sufficiency("q", "ctx") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `rtk pytest tests/mcp/test_ask_self_correct.py -v`
Expected: FAIL with `AttributeError: module 'axon.mcp.server' has no attribute '_self_correct_enabled'`

- [ ] **Step 3: Write minimal implementation**

Add imports at the top of `server.py` (near the other `axon.router` imports):

```python
import json
import litellm
from axon.router.engine import _bottom_tier_model
from axon.router.llm_backend import litellm_kwargs
from axon.retrieval.self_correct import correct_retrieval
```

Add these module-level helpers (place near `_retrieve_context`):

```python
def _self_correct_enabled() -> bool:
    return os.getenv("AXON_SELF_CORRECT", "1").strip().lower() not in ("0", "false", "no", "off")


def _cheap_llm_json(system: str, user: str) -> dict:
    """One cheap FREE-profile completion returning parsed JSON, or {} on failure."""
    model = _bottom_tier_model()
    kwargs = litellm_kwargs(model, ollama_host=_RUNTIME.ollama_local_host,
                            num_ctx=_RUNTIME.scoring_num_ctx)
    try:
        response = litellm.completion(
            **kwargs, temperature=0, response_format={"type": "json_object"},
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
        )
        return json.loads(response.choices[0].message.content)
    except Exception:
        return {}


def _judge_sufficiency(query: str, context: str) -> bool:
    payload = _cheap_llm_json(
        "Voce julga se o CONTEXTO responde a PERGUNTA. Responda JSON "
        '{"sufficient": true|false}. true so se o contexto claramente basta.',
        f"PERGUNTA:\n{query}\n\nCONTEXTO:\n{context}",
    )
    return bool(payload.get("sufficient", False))


def _reformulate_query(query: str) -> str:
    payload = _cheap_llm_json(
        "Reescreva a busca para melhorar recuperacao (sinonimos, termos mais "
        'especificos). Responda JSON {"query": "..."}.',
        query,
    )
    rewritten = str(payload.get("query", "")).strip()
    return rewritten or query


async def _graph_fallback(hits: list[dict]) -> str:
    if not hits:
        return ""
    symbol = (hits[0].get("payload") or {}).get("symbol")
    if not symbol:
        return ""
    store = _get_session_store()
    await store.init()
    subgraph = await store.query_subgraph(symbol, depth=2)
    edges = subgraph.get("edges") or []
    if not edges:
        return ""
    lines = "\n".join(f"{e['source']} -> {e['target']}" for e in edges[:10])
    return f"## Dependencias relacionadas (grafo)\nRoot: {symbol}\n{lines}"
```

In `ask()`, replace the retrieval call site (currently `code_context, pack, hits = await _retrieve_context(...)` at ~line 587-594) so the correction runs right after:

```python
    code_context, pack, hits = await _retrieve_context(
        query=query, ctx=effective_ctx, language=None,
        max_depth=2, max_nodes=25,
        max_tokens=rtk_max_tokens if rtk_max_tokens is not None else _RTK_MAX_TOKENS,
    )

    async def _retry(q: str):
        return await _retrieve_context(
            query=q, ctx=effective_ctx, language=None,
            max_depth=2, max_nodes=25,
            max_tokens=rtk_max_tokens if rtk_max_tokens is not None else _RTK_MAX_TOKENS,
        )

    correction = await correct_retrieval(
        query, effective_ctx, code_context, pack, hits,
        retrieve_fn=_retry, judge_fn=_judge_sufficiency,
        reformulate_fn=_reformulate_query, graph_fn=_graph_fallback,
        enabled=_self_correct_enabled(),
    )
    code_context, pack, hits = correction.code_context, correction.pack, correction.hits
    if trace is not None:
        trace.append_stage("self_correct", payload=correction.meta)
```

(Leave the existing `if trace is not None: trace.append_stage("retrieval", ...)` block that follows unchanged.)

- [ ] **Step 4: Run test to verify it passes**

Run: `rtk pytest tests/mcp/test_ask_self_correct.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Run the broader suite to confirm no regression**

Run: `rtk pytest tests/mcp -q && rtk python3 -m compileall src/axon/mcp/server.py`
Expected: PASS, no import errors.

- [ ] **Step 6: Commit**

```bash
git add src/axon/mcp/server.py tests/mcp/test_ask_self_correct.py
git commit -m "feat(mcp): wire self-correcting retrieval into ask() with kill-switch"
```

---

### Task 4: Retrieval benchmark + golden set

**Files:**
- Create: `src/axon/benchmark/retrieval_eval.py`
- Create: `tests/benchmark/fixtures/retrieval_golden.json`
- Test: `tests/benchmark/test_retrieval_eval.py`

**Interfaces:**
- Consumes: `correct_retrieval` (Task 2).
- Produces:
  - `@dataclass(frozen=True) class GoldenCase: query: str; ctx: str; expected_symbols: frozenset[str]`
  - `def load_golden(path: str) -> list[GoldenCase]`
  - `def symbols_of(hits: list[dict]) -> set[str]`
  - `def recall(expected: frozenset[str], hits: list[dict]) -> float`
  - `async def evaluate(cases, first_pass_fn, correct_fn) -> dict` — returns `{"recall_first", "recall_after", "delta", "retry_rate", "give_up_rate", "n"}`.
    - `first_pass_fn: Callable[[GoldenCase], Awaitable[tuple[str, object, list[dict]]]]`
    - `correct_fn: Callable[[GoldenCase, str, object, list[dict]], Awaitable[CorrectionResult]]`

- [ ] **Step 1: Write the failing test**

```python
# tests/benchmark/test_retrieval_eval.py
import pytest
from axon.benchmark.retrieval_eval import (
    GoldenCase, symbols_of, recall, evaluate, load_golden,
)
from axon.retrieval.self_correct import CorrectionResult


def test_symbols_of_extracts_payload_symbols():
    assert symbols_of([{"payload": {"symbol": "A"}}, {"payload": {"symbol": "B"}}]) == {"A", "B"}


def test_recall_full_and_partial():
    assert recall(frozenset({"A", "B"}), [{"payload": {"symbol": "A"}}, {"payload": {"symbol": "B"}}]) == 1.0
    assert recall(frozenset({"A", "B"}), [{"payload": {"symbol": "A"}}]) == 0.5
    assert recall(frozenset(), []) == 1.0  # nothing expected -> trivially satisfied


def test_load_golden_reads_fixture(tmp_path):
    p = tmp_path / "g.json"
    p.write_text('[{"query": "q", "ctx": "personal", "expected_symbols": ["A"]}]')
    cases = load_golden(str(p))
    assert cases == [GoldenCase("q", "personal", frozenset({"A"}))]


@pytest.mark.asyncio
async def test_evaluate_computes_delta_and_rates():
    cases = [GoldenCase("q1", "personal", frozenset({"A"}))]

    async def first_pass(case):
        return ("CTX", object(), [{"score": 0.05, "payload": {"symbol": "Z"}}])

    async def correct(case, cc, pack, hits):
        return CorrectionResult("CTX2", pack, [{"payload": {"symbol": "A"}}],
                                {"retried": True, "gave_up": False,
                                 "strategy_used": "reformulate", "verdict": "low_score"})

    report = await evaluate(cases, first_pass, correct)
    assert report["recall_first"] == 0.0
    assert report["recall_after"] == 1.0
    assert report["delta"] == 1.0
    assert report["retry_rate"] == 1.0
    assert report["give_up_rate"] == 0.0
    assert report["n"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `rtk pytest tests/benchmark/test_retrieval_eval.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'axon.benchmark.retrieval_eval'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/axon/benchmark/retrieval_eval.py
"""Retrieval-quality benchmark for the self-correcting loop. Measures recall@k
before vs after correction over a golden set. Distinct from model_eval.py, which
compares models, not retrieval.

See docs/superpowers/specs/2026-07-01-agentic-retrieval-design.md (D-E).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Awaitable, Callable

from axon.retrieval.self_correct import CorrectionResult


@dataclass(frozen=True)
class GoldenCase:
    query: str
    ctx: str
    expected_symbols: frozenset[str]


def load_golden(path: str) -> list[GoldenCase]:
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    return [
        GoldenCase(c["query"], c["ctx"], frozenset(c["expected_symbols"]))
        for c in raw
    ]


def symbols_of(hits: list[dict]) -> set[str]:
    out: set[str] = set()
    for h in hits:
        sym = (h.get("payload") or {}).get("symbol")
        if sym:
            out.add(sym)
    return out


def recall(expected: frozenset[str], hits: list[dict]) -> float:
    if not expected:
        return 1.0
    found = expected & symbols_of(hits)
    return len(found) / len(expected)


async def evaluate(
    cases: list[GoldenCase],
    first_pass_fn: "Callable[[GoldenCase], Awaitable[tuple[str, object, list[dict]]]]",
    correct_fn: "Callable[[GoldenCase, str, object, list[dict]], Awaitable[CorrectionResult]]",
) -> dict:
    n = len(cases)
    if n == 0:
        return {"recall_first": 0.0, "recall_after": 0.0, "delta": 0.0,
                "retry_rate": 0.0, "give_up_rate": 0.0, "n": 0}
    r_first = r_after = retries = gave_up = 0.0
    for case in cases:
        cc, pack, hits = await first_pass_fn(case)
        r_first += recall(case.expected_symbols, hits)
        result = await correct_fn(case, cc, pack, hits)
        r_after += recall(case.expected_symbols, result.hits)
        retries += 1.0 if result.meta.get("retried") else 0.0
        gave_up += 1.0 if result.meta.get("gave_up") else 0.0
    return {
        "recall_first": r_first / n,
        "recall_after": r_after / n,
        "delta": (r_after - r_first) / n,
        "retry_rate": retries / n,
        "give_up_rate": gave_up / n,
        "n": n,
    }
```

Create the seed golden set (3 real cases; expand later — this is the calibration input for Task 5):

```json
// tests/benchmark/fixtures/retrieval_golden.json
[
  {"query": "como o ask roteia contexto por projeto", "ctx": "personal", "expected_symbols": ["ContextDetector", "ask"]},
  {"query": "quem usa PolicyRegistry", "ctx": "personal", "expected_symbols": ["PolicyRegistry"]},
  {"query": "onde a supersessao penaliza rank", "ctx": "personal", "expected_symbols": ["has_revision_verb"]}
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `rtk pytest tests/benchmark/test_retrieval_eval.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/axon/benchmark/retrieval_eval.py tests/benchmark/test_retrieval_eval.py tests/benchmark/fixtures/retrieval_golden.json
git commit -m "feat(benchmark): retrieval recall-delta eval + seed golden set"
```

---

### Task 5: Calibrate LOW/HIGH against the golden set

**Files:**
- Create: `scripts/calibrate_retrieval_bands.py`
- Modify: `src/axon/retrieval/self_correct.py` (update `LOW`/`HIGH` constants + add a comment recording the calibration date and golden-set size)

**Interfaces:**
- Consumes: `load_golden`, `evaluate` (Task 4); `_retrieve_context` and the `ask()` wiring (Task 3).

This task is an offline calibration, not a TDD unit — the deliverable is chosen constant values backed by a runnable script.

- [ ] **Step 1: Write the calibration script**

```python
# scripts/calibrate_retrieval_bands.py
"""Sweep LOW/HIGH candidates over the golden set and print the recall delta and
give-up rate per band, so the maintainer can pick constants. Run manually:

    rtk python3 scripts/calibrate_retrieval_bands.py
"""
import asyncio

import axon.retrieval.self_correct as sc
from axon.benchmark.retrieval_eval import evaluate, load_golden
from axon.mcp.server import (
    _graph_fallback, _judge_sufficiency, _reformulate_query, _retrieve_context,
)

GOLDEN = "tests/benchmark/fixtures/retrieval_golden.json"
CANDIDATES = [(0.25, 0.55), (0.35, 0.65), (0.45, 0.75)]


async def _first_pass(case):
    return await _retrieve_context(
        query=case.query, ctx=case.ctx, language=None,
        max_depth=2, max_nodes=25, max_tokens=1200,
    )


async def _correct(case, cc, pack, hits):
    async def _retry(q):
        return await _retrieve_context(
            query=q, ctx=case.ctx, language=None,
            max_depth=2, max_nodes=25, max_tokens=1200,
        )
    return await sc.correct_retrieval(
        case.query, case.ctx, cc, pack, hits,
        retrieve_fn=_retry, judge_fn=_judge_sufficiency,
        reformulate_fn=_reformulate_query, graph_fn=_graph_fallback,
    )


async def main():
    cases = load_golden(GOLDEN)
    for low, high in CANDIDATES:
        sc.LOW, sc.HIGH = low, high
        report = await evaluate(cases, _first_pass, _correct)
        print(f"LOW={low} HIGH={high} -> {report}")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Run the calibration**

Run: `rtk python3 scripts/calibrate_retrieval_bands.py`
Expected: three lines, one per candidate band, showing `recall_first`, `recall_after`, `delta`, `retry_rate`, `give_up_rate`. Pick the band with the best `delta` at an acceptable `give_up_rate` (do not maximize retries blindly — a band that retries every query but barely moves recall is worse than a tighter one).

- [ ] **Step 3: Record the chosen constants**

Edit `src/axon/retrieval/self_correct.py`, set `LOW`/`HIGH` to the chosen values, and update the comment above them:

```python
# Calibrated 2026-07-01 against tests/benchmark/fixtures/retrieval_golden.json
# (N cases). Re-run scripts/calibrate_retrieval_bands.py when the golden set grows.
LOW: float = <chosen>
HIGH: float = <chosen>
```

- [ ] **Step 4: Confirm the cascade tests still pass with the new bands**

Run: `rtk pytest tests/retrieval/test_self_correct_cascade.py -v`
Expected: PASS. If a band boundary test now straddles the new constants, update the test's score inputs to keep testing empty/low/gray/high — do not weaken the assertions.

- [ ] **Step 5: Commit**

```bash
git add scripts/calibrate_retrieval_bands.py src/axon/retrieval/self_correct.py tests/retrieval/test_self_correct_cascade.py
git commit -m "chore(retrieval): calibrate LOW/HIGH bands against golden set"
```

---

## Self-Review

**Spec coverage:**
- D-A (hybrid cascade) → Task 1 (`grade`, bands) + Task 5 (calibration). ✓
- D-B (one retry, strategy by query shape) → Task 2 (`correct_retrieval`, `is_structural`). ✓
- D-C (1-retry cap + honest give-up) → Task 2 (give-up header, no second retry). ✓
- D-D (internal, extracted module, kill-switch) → Task 1/2 (module) + Task 3 (`ask()` wiring, `AXON_SELF_CORRECT`). ✓
- D-E (retrieval benchmark, not model_eval) → Task 4. ✓
- Observability (trace stage `self_correct`) → Task 3. ✓
- Open item "verify hits carry score" → resolved before planning (cosine `score` confirmed). ✓
- Open item "judge model / _POLICY" → Task 3 (`_bottom_tier_model` via `litellm_kwargs`). ✓
- Open item "query-shape classifier, no model" → Task 1 (`is_structural`, regex + phrases). ✓
- Open item "golden set" → Task 4 (seed) + Task 5 (calibration). ✓

**Placeholder scan:** No TBD/TODO. The only intentional `<chosen>` markers are in Task 5, whose deliverable *is* choosing them via the runnable script — that is the task, not a gap.

**Type consistency:** `CorrectionResult(code_context, pack, hits, meta)` defined in Task 2, consumed identically in Task 3 (`correction.code_context/.pack/.hits/.meta`) and Task 4 (`result.hits`, `result.meta`). `grade(...) -> (bool, str)` defined Task 1, used Task 2. `_bottom_tier_model`, `litellm_kwargs`, `query_subgraph` match the real signatures verified in the code.
