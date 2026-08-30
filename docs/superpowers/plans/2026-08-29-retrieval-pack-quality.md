# Retrieval Pack Quality Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make pack quality measurable, then raise it by enabling the reranker that already exists — without letting a dead reranker look like a working one.

**Architecture:** A committed fixture of question/expected-files cases drives a pure scoring module and a script that runs it against the live store. Pack assembly gains a dedup pass. The cross-encoder rerank moves from opt-in to default, with a bounded load and a telemetry field that records whether it actually ran.

**Tech Stack:** Python 3.11+, pydantic v2, pytest, asyncpg/pgvector, fastembed `TextCrossEncoder`.

**Spec:** `docs/superpowers/specs/2026-08-29-retrieval-pack-quality-design.md`

## Global Constraints

- Python 3.11+ with type hints on all signatures; pydantic v2 for persisted models.
- Tests run `python3 -m pytest ... -p no:rerunfailures` (the plugin binds a socket, which the sandbox denies).
- Never weaken `PROTECTED_CONTEXTS`. `ctx=work` must not reach any telemetry file.
- Telemetry must never break retrieval: every recording path stays inside `try/except` that logs.
- `AXON_PG_URL` for local runs: `postgresql://axon:axon@localhost:5434/axon`.
- Model downloads need `HF_HUB_DISABLE_XET=1` on this machine or they hang forever.
- Do not commit `data/**` telemetry files; `data/compression/stats.jsonl` carries `skip-worktree`.

## Amendments (2026-08-30)

- **The acceptance instrument was wrong, and it was a specification defect.** Task 3 Step 6 and Task 4 Step 8 both compare `scripts/eval_pack_quality.py` against the Task 1 baseline, but that script queries the store directly and never enters `_retrieve_context`, where `dedup_hits` and `_rerank_hits` live. It returned an identical 0.625 / 0.402 across Tasks 1-3. The script now has a `--pack-path` mode that routes through `_retrieve_context`; the default store-only arm is frozen so the 0.625 / 0.402 baseline stays comparable.
- **Task 4's acceptance criterion, restated.** With `--pack-path`, hit rate must rise from about 0.625 to about 0.717. That ~+0.09 is above the noise floor for this corpus, which holds about 92 unique `(query, expected_files)` pairs out of 120 rows. The store-only arm must NOT move; if it does, something else changed. `scripts/eval_nl_code_recall.py` must stay at or above 18/24.
- **Authorized edits to three pre-existing tests.** The plan changes `_RERANK_CANDIDATES` 24 -> 48, `_RERANK_TEXT_CHARS` 1200 -> 400 and the rerank default off -> on, and never named the tests pinning the old values: `tests/mcp/test_retrieval_tools.py` at line 352 (whose fixture relied on an unset `AXON_RERANK` meaning off), lines 431-432 and line 621. The repo owner authorized updating them to the new values without weakening what each detects; the off-test now proves the `AXON_RERANK=0` opt-out rather than a default. `tests/conftest.py` also pins `AXON_RERANK=0` for the suite so unit tests never load a real cross-encoder.
- **The fixture is not reproducible, and that is a known limitation.** `gen_pack_quality_fixture.py` selects with `ORDER BY created_at DESC LIMIT` and no cutoff, so regenerating it later returns a different set of decisions - observed during this build, because the build's own commits wrote new ones. The committed file is the ruler; do not regenerate it to "refresh" it.

---

### Task 1: Pack-quality scoring module and fixture

**Files:**
- Create: `src/axon/benchmark/pack_eval.py`
- Create: `tests/benchmark/test_pack_eval.py`
- Create: `scripts/gen_pack_quality_fixture.py`
- Create: `tests/benchmark/fixtures/pack_quality_golden.json` (generated, committed)

**Interfaces:**
- Consumes: nothing.
- Produces: `pack_hit(expected_files: set[str], hit_paths: list[str]) -> bool`,
  `pack_coverage(expected_files: set[str], hit_paths: list[str]) -> float`,
  `basenames(paths: Iterable[str]) -> set[str]`,
  `PackCase(query: str, expected_files: list[str], decision_id: str)` (pydantic model),
  `load_cases(path: Path) -> list[PackCase]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/benchmark/test_pack_eval.py
from __future__ import annotations

import json
from pathlib import Path

import pytest


def test_basenames_strips_directories() -> None:
    from axon.benchmark.pack_eval import basenames

    assert basenames(["src/axon/store/collections.py", "a/b/x.md"]) == {
        "collections.py",
        "x.md",
    }


def test_pack_hit_is_true_when_any_expected_file_is_present() -> None:
    from axon.benchmark.pack_eval import pack_hit

    assert pack_hit({"a.py", "b.py"}, ["z.py", "a.py"]) is True


def test_pack_hit_is_false_when_none_present() -> None:
    from axon.benchmark.pack_eval import pack_hit

    assert pack_hit({"a.py"}, ["z.py", "y.py"]) is False


def test_pack_coverage_is_the_fraction_of_expected_files_found() -> None:
    from axon.benchmark.pack_eval import pack_coverage

    assert pack_coverage({"a.py", "b.py", "c.py"}, ["a.py", "c.py", "z.py"]) == pytest.approx(2 / 3)


def test_pack_coverage_of_empty_expectation_is_zero_not_a_crash() -> None:
    from axon.benchmark.pack_eval import pack_coverage

    assert pack_coverage(set(), ["a.py"]) == 0.0


def test_duplicate_hits_do_not_inflate_coverage() -> None:
    """A pack repeating one file must not score as if it found two."""
    from axon.benchmark.pack_eval import pack_coverage

    assert pack_coverage({"a.py", "b.py"}, ["a.py", "a.py", "a.py"]) == pytest.approx(0.5)


def test_load_cases_reads_the_committed_fixture(tmp_path: Path) -> None:
    from axon.benchmark.pack_eval import load_cases

    fixture = tmp_path / "cases.json"
    fixture.write_text(
        json.dumps(
            [{"query": "why does x break", "expected_files": ["a.py"], "decision_id": "dec-1"}]
        ),
        encoding="utf-8",
    )
    cases = load_cases(fixture)
    assert len(cases) == 1
    assert cases[0].query == "why does x break"
    assert cases[0].expected_files == ["a.py"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/benchmark/test_pack_eval.py -q -p no:rerunfailures`
Expected: FAIL — `ModuleNotFoundError: No module named 'axon.benchmark.pack_eval'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/axon/benchmark/pack_eval.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/benchmark/test_pack_eval.py -q -p no:rerunfailures`
Expected: PASS (7 passed)

- [ ] **Step 5: Write the fixture generator**

```python
# scripts/gen_pack_quality_fixture.py
"""Generate the pack-quality case fixture ONCE, from the decision store.

The fixture is committed and read by the eval. It is deliberately not queried
live: a ruler that grows new cases every week silently stops being comparable
to last week's run, which is how docs/METRICS.md ended up publishing a p50 over
a filter that selected the events that had worked.

Usage:
    AXON_PG_URL=postgresql://axon:axon@localhost:5434/axon \
        python3 scripts/gen_pack_quality_fixture.py --limit 120
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

OUT = REPO_ROOT / "tests" / "benchmark" / "fixtures" / "pack_quality_golden.json"

QUERY = """
    SELECT id,
           frontmatter->>'summary' AS summary,
           frontmatter->'files'    AS files
    FROM decisions
    WHERE frontmatter->>'repo' = 'axon'
      AND jsonb_array_length(COALESCE(frontmatter->'files', '[]')) BETWEEN 1 AND 6
      AND length(frontmatter->>'summary') > 25
    ORDER BY created_at DESC
    LIMIT $1
"""


async def main() -> None:
    import asyncpg

    parser = argparse.ArgumentParser(description="Build the pack-quality fixture.")
    parser.add_argument("--limit", type=int, default=120)
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument(
        "--dsn", default=os.environ.get("AXON_PG_URL", "postgresql://axon:axon@localhost:5434/axon")
    )
    args = parser.parse_args()

    con = await asyncpg.connect(args.dsn)
    try:
        rows = await con.fetch(QUERY, args.limit)
    finally:
        await con.close()

    cases = [
        {
            "query": row["summary"],
            "expected_files": json.loads(row["files"]),
            "decision_id": row["id"],
        }
        for row in rows
    ]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(cases, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {len(cases)} cases to {args.out}")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 6: Generate and inspect the fixture**

Run:
```bash
AXON_PG_URL=postgresql://axon:axon@localhost:5434/axon \
  python3 scripts/gen_pack_quality_fixture.py --limit 120
python3 -c "import json;d=json.load(open('tests/benchmark/fixtures/pack_quality_golden.json'));print(len(d), d[0])"
```
Expected: around 120 cases; each has a non-empty `query` and 1-6 `expected_files`.

- [ ] **Step 7: Write the eval script**

```python
# scripts/eval_pack_quality.py
"""Does the pack contain the files the work actually touched?

Baseline measured 2026-08-29 (n=60, top_k=8, four collections): hit rate 0.583,
coverage 0.384. See docs/superpowers/specs/2026-08-29-retrieval-pack-quality-design.md.

Usage:
    AXON_PG_URL=postgresql://axon:axon@localhost:5434/axon \
        HF_HUB_DISABLE_XET=1 python3 scripts/eval_pack_quality.py --top-k 8
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

FIXTURE = REPO_ROOT / "tests" / "benchmark" / "fixtures" / "pack_quality_golden.json"
COLLECTIONS = ["personal", "career", "knowledge", "saas"]


async def main() -> None:
    parser = argparse.ArgumentParser(description="Pack quality against the committed fixture.")
    parser.add_argument("--fixture", type=Path, default=FIXTURE)
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument(
        "--dsn", default=os.environ.get("AXON_PG_URL", "postgresql://axon:axon@localhost:5434/axon")
    )
    args = parser.parse_args()

    from axon.benchmark.pack_eval import basenames, load_cases, pack_coverage, pack_hit
    from axon.embedder.engine import EmbedderEngine
    from axon.store.pg_vector_store import PgVectorStore

    cases = load_cases(args.fixture)
    engine = EmbedderEngine()
    store = PgVectorStore(dsn=args.dsn)
    hits = 0
    coverage = 0.0
    try:
        for case in cases:
            results = await store.search(
                query_vector=engine.embed_one(case.query),
                query=case.query,
                collections=COLLECTIONS,
                top_k=args.top_k,
                max_nodes=args.top_k,
                max_tokens=10**9,
            )
            paths = [str((h.get("payload") or {}).get("file_path", "")) for h in results]
            expected = basenames(case.expected_files)
            hits += 1 if pack_hit(expected, paths) else 0
            coverage += pack_coverage(expected, paths)
    finally:
        await store.close()

    n = len(cases)
    print(f"cases: {n}   top_k: {args.top_k}")
    print(f"hit rate: {hits}/{n} = {hits / n:.3f}")
    print(f"coverage: {coverage / n:.3f}")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 8: Run the eval to record the baseline**

Run:
```bash
AXON_PG_URL=postgresql://axon:axon@localhost:5434/axon \
  python3 scripts/eval_pack_quality.py --top-k 8
```
Expected: a hit rate near 0.55-0.60 and coverage near 0.30-0.40. These are the
design-time figures for n=60; the fixture holds ~120 cases, so the exact values
will differ and only this run's numbers are the baseline. Write them into the PR
description — every later task is compared against them, not against the spec.

- [ ] **Step 9: Commit**

```bash
git add src/axon/benchmark/pack_eval.py tests/benchmark/test_pack_eval.py \
        scripts/gen_pack_quality_fixture.py scripts/eval_pack_quality.py \
        tests/benchmark/fixtures/pack_quality_golden.json
git commit -m "feat(benchmark): measure whether a pack reaches the files the work touched"
```

---

### Task 2: Record the query on recall telemetry, except for work

**Files:**
- Modify: `src/axon/observability/recall_telemetry.py:46-53` (`ChunkRecord`)
- Modify: `src/axon/mcp/server.py:188-224` (`_record_chunk_recall`)
- Modify: `src/axon/mcp/server.py:505-511` (the `_record_chunk_recall` call site)
- Test: `tests/observability/test_recall_query_capture.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: `ChunkRecord.query: str | None = None`;
  `_record_chunk_recall(*, query: str, strategy_name: str, requested_max_tokens: int, hits: list[dict], ctx: str | None = None) -> None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/observability/test_recall_query_capture.py
from __future__ import annotations

import json
from pathlib import Path

import pytest


def _chunks_written(tmp_path: Path) -> list[dict]:
    # RuntimeConfig.data_root is engine_root / "data", and engine_root comes
    # from AXON_ENGINE (src/axon/config/runtime.py:148).
    chunks_file = tmp_path / "data" / "recall" / "chunks.jsonl"
    if not chunks_file.exists():
        return []
    return [json.loads(line) for line in chunks_file.read_text(encoding="utf-8").splitlines() if line]


@pytest.fixture()
def data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("AXON_ENGINE", str(tmp_path))
    return tmp_path


def test_query_is_recorded_for_a_normal_ctx(data_root: Path) -> None:
    from axon.mcp import server

    server._record_chunk_recall(
        query="onde os vetores sao removidos",
        strategy_name="balanced",
        requested_max_tokens=4000,
        hits=[{"payload": {"content": "x", "file_path": "a.py"}, "score": 0.5}],
        ctx="knowledge",
    )
    rows = _chunks_written(data_root)
    assert rows and rows[0]["query"] == "onde os vetores sao removidos"


def test_query_is_never_recorded_for_work(data_root: Path) -> None:
    """dec-109/dec-131: work isolation must not leak through a telemetry file."""
    from axon.mcp import server

    server._record_chunk_recall(
        query="segredo do cliente avangrid",
        strategy_name="balanced",
        requested_max_tokens=4000,
        hits=[{"payload": {"content": "x", "file_path": "a.py"}, "score": 0.5}],
        ctx="work",
    )
    rows = _chunks_written(data_root)
    assert rows, "the record must still be written - only the query is withheld"
    assert rows[0]["query"] is None
    assert "avangrid" not in json.dumps(rows[0]).lower()


def test_query_hash_is_still_recorded_for_work(data_root: Path) -> None:
    """The hash carries no plaintext and keeps work rows countable."""
    from axon.mcp import server

    server._record_chunk_recall(
        query="segredo do cliente avangrid",
        strategy_name="balanced",
        requested_max_tokens=4000,
        hits=[{"payload": {"content": "x", "file_path": "a.py"}, "score": 0.5}],
        ctx="work",
    )
    rows = _chunks_written(data_root)
    assert rows[0]["query_hash"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/observability/test_recall_query_capture.py -q -p no:rerunfailures`
Expected: FAIL — `TypeError: _record_chunk_recall() got an unexpected keyword argument 'ctx'`

If the runtime caches its config at import time, the fixture must set
`AXON_ENGINE` before `axon.mcp.server` is first imported, or reload the module.
Do not change production code to fit the test.

- [ ] **Step 3: Add the field to ChunkRecord**

```python
# src/axon/observability/recall_telemetry.py - inside class ChunkRecord
    ts: str
    query_hash: str
    #: The query itself, so a later eval can be built from real traffic instead
    #: of derived cases. None for protected contexts (dec-109) and on legacy
    #: rows written before this field existed.
    query: str | None = None
    strategy: str
    requested_max_tokens: int
    chunks: list[ChunkEntry]
```

- [ ] **Step 4: Thread ctx through the recorder**

```python
# src/axon/mcp/server.py - _record_chunk_recall signature and record construction
def _record_chunk_recall(
    *,
    query: str,
    strategy_name: str,
    requested_max_tokens: int,
    hits: list[dict],
    ctx: str | None = None,
) -> None:
```

```python
        record = ChunkRecord(
            ts=datetime.now(UTC).isoformat(),
            query_hash=_sha256_16(query),
            # Never persist the text of a protected-context query.
            query=None if normalize_context(ctx) in PROTECTED_CONTEXTS else query,
            strategy=strategy_name,
            requested_max_tokens=requested_max_tokens,
            chunks=chunks,
        )
```

Add to the imports at the top of `src/axon/mcp/server.py`:

```python
from axon.context.registry import PROTECTED_CONTEXTS, normalize_context
```

- [ ] **Step 5: Pass ctx at the call site**

```python
# src/axon/mcp/server.py - inside _retrieve_context
    _record_chunk_recall(
        query=query,
        strategy_name=strategy.name,
        requested_max_tokens=max_tokens,
        hits=telemetry_hits,
        ctx=ctx,
    )
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python3 -m pytest tests/observability/ tests/mcp/ -q -p no:rerunfailures`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/axon/observability/recall_telemetry.py src/axon/mcp/server.py \
        tests/observability/test_recall_query_capture.py
git commit -m "feat(observability): record the query behind a recall, never for work"
```

---

### Task 3: Dedup and cap the pack

**Files:**
- Create: `src/axon/context/pack_dedup.py`
- Create: `tests/context/test_pack_dedup.py`
- Modify: `src/axon/mcp/server.py:484-486` (apply before pack assembly)

**Interfaces:**
- Consumes: nothing from Tasks 1-2.
- Produces: `dedup_hits(hits: list[dict], *, max_per_file: int = 2) -> list[dict]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/context/test_pack_dedup.py
from __future__ import annotations


def _hit(content: str, file_path: str, score: float = 0.5) -> dict:
    return {"score": score, "payload": {"content": content, "file_path": file_path}}


def test_identical_content_is_dropped() -> None:
    from axon.context.pack_dedup import dedup_hits

    out = dedup_hits([_hit("same", "a.py"), _hit("same", "b.py")])
    assert len(out) == 1


def test_at_most_two_chunks_per_file() -> None:
    """Two, not one: a large file can legitimately answer in more than one place."""
    from axon.context.pack_dedup import dedup_hits

    out = dedup_hits([_hit(f"c{i}", "a.py") for i in range(5)])
    assert len(out) == 2


def test_relative_order_is_preserved() -> None:
    from axon.context.pack_dedup import dedup_hits

    hits = [_hit("one", "a.py", 0.9), _hit("two", "b.py", 0.8), _hit("three", "c.py", 0.7)]
    assert [h["payload"]["content"] for h in dedup_hits(hits)] == ["one", "two", "three"]


def test_the_first_occurrence_wins() -> None:
    """Hits arrive ranked, so the kept copy must be the higher-ranked one."""
    from axon.context.pack_dedup import dedup_hits

    out = dedup_hits([_hit("same", "a.py", 0.9), _hit("same", "b.py", 0.1)])
    assert out[0]["score"] == 0.9


def test_empty_input_is_empty_output() -> None:
    from axon.context.pack_dedup import dedup_hits

    assert dedup_hits([]) == []


def test_missing_payload_does_not_crash() -> None:
    from axon.context.pack_dedup import dedup_hits

    assert len(dedup_hits([{"score": 0.5}, {"score": 0.4}])) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/context/test_pack_dedup.py -q -p no:rerunfailures`
Expected: FAIL — `ModuleNotFoundError: No module named 'axon.context.pack_dedup'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/axon/context/pack_dedup.py
"""Drop pack slots that carry nothing new.

Measured 2026-08-29 over 40 questions: 10.0% of slots held byte-identical
content and 13.4% repeated a file. Small on its own; the point is that those
slots are the ones the reranker wants.
"""

from __future__ import annotations

_DEFAULT_MAX_PER_FILE = 2


def dedup_hits(hits: list[dict], *, max_per_file: int = _DEFAULT_MAX_PER_FILE) -> list[dict]:
    """Keep the first occurrence of each content, at most max_per_file per file.

    Hits arrive ranked, so first-wins keeps the better-ranked copy. Order is
    preserved: this drops slots, it never reorders them.
    """
    seen_content: set[str] = set()
    per_file: dict[str, int] = {}
    kept: list[dict] = []
    for hit in hits:
        payload = hit.get("payload") or {}
        content = str(payload.get("content", ""))
        file_path = str(payload.get("file_path", ""))
        if content in seen_content:
            continue
        if per_file.get(file_path, 0) >= max_per_file:
            continue
        seen_content.add(content)
        per_file[file_path] = per_file.get(file_path, 0) + 1
        kept.append(hit)
    return kept
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/context/test_pack_dedup.py -q -p no:rerunfailures`
Expected: PASS (6 passed)

- [ ] **Step 5: Apply it in the retrieval path**

In `src/axon/mcp/server.py`, immediately after the `store.search(...)` call and
**before** the `if rerank:` block:

```python
    results = dedup_hits(results)
```

Before the rerank, not after: a duplicate that survives into the candidate pool
costs cross-encoder time and then gets dropped anyway, leaving fewer than the
intended slots. Task 4 rewrites the `if rerank:` block below this line and must
leave this call where it is.

Add to the imports at the top of `src/axon/mcp/server.py`:

```python
from axon.context.pack_dedup import dedup_hits
```

- [ ] **Step 6: Run the suite and the eval**

Run:
```bash
python3 -m pytest tests/ -q -p no:rerunfailures
AXON_PG_URL=postgresql://axon:axon@localhost:5434/axon python3 scripts/eval_pack_quality.py --top-k 8
```
Expected: suite green; hit rate and coverage at or above the Task 1 baseline.
If either metric drops, revert this task rather than explaining the drop.

- [ ] **Step 7: Commit**

```bash
git add src/axon/context/pack_dedup.py tests/context/test_pack_dedup.py src/axon/mcp/server.py
git commit -m "feat(context): stop spending pack slots on content already in the pack"
```

---

### Task 4: Enable the reranker with a bounded load and loud failure

**Files:**
- Modify: `src/axon/mcp/server.py:82-84` (constants)
- Modify: `src/axon/mcp/server.py:105-116` (`_get_reranker`)
- Modify: `src/axon/mcp/server.py:420-446` (`_rerank_enabled`, `_rerank_hits`)
- Modify: `src/axon/mcp/server.py:458-483` (`_retrieve_context` rerank block)
- Modify: `src/axon/observability/recall_telemetry.py` (`ChunkRecord.rerank`)
- Test: `tests/mcp/test_rerank_contract.py`

**Interfaces:**
- Consumes: `_record_chunk_recall(..., ctx=...)` from Task 2; `dedup_hits` from Task 3.
- Produces: `_rerank_hits(query: str, hits: list[dict]) -> tuple[list[dict], str | None]`
  — the second element is `None` when the rerank ran, otherwise a short reason
  string. `ChunkRecord.rerank: str | None = None` carries that reason.
- Breaking change: `_rerank_hits` used to return `list[dict]`. The only caller is
  `_retrieve_context`.

- [ ] **Step 1: Write the failing test**

```python
# tests/mcp/test_rerank_contract.py
from __future__ import annotations

import pytest


def _hits() -> list[dict]:
    return [
        {"score": 0.9, "payload": {"content": "irrelevant text", "file_path": "z.py"}},
        {"score": 0.8, "payload": {"content": "delete_by_file removes vectors", "file_path": "a.py"}},
    ]


def test_rerank_returns_a_reason_when_the_model_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """A rerank that did not run must be distinguishable from one that did.

    This is the judge-is-dead defect: except Exception returned the input order
    and the pack looked identical to a reranked one.
    """
    from axon.mcp import server

    def boom() -> object:
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(server, "_get_reranker", boom)
    hits, reason = server._rerank_hits("query", _hits())
    assert reason is not None
    assert [h["score"] for h in hits] == [0.9, 0.8], "input order must survive"


def test_rerank_returns_no_reason_when_it_ran(monkeypatch: pytest.MonkeyPatch) -> None:
    from axon.mcp import server

    class FakeReranker:
        def rerank_pairs(self, pairs: list[tuple[str, str]]) -> list[float]:
            return [0.1, 9.9]

    monkeypatch.setattr(server, "_get_reranker", lambda: FakeReranker())
    hits, reason = server._rerank_hits("query", _hits())
    assert reason is None
    assert hits[0]["payload"]["file_path"] == "a.py", "the reranker's order must win"


def test_rerank_on_empty_hits_is_a_no_op() -> None:
    from axon.mcp import server

    hits, reason = server._rerank_hits("query", [])
    assert hits == []
    assert reason is None


def test_rerank_is_enabled_by_default() -> None:
    from axon.mcp import server

    assert server._rerank_enabled() is True


def test_rerank_can_be_turned_off(monkeypatch: pytest.MonkeyPatch) -> None:
    from axon.mcp import server

    monkeypatch.setenv("AXON_RERANK", "0")
    assert server._rerank_enabled() is False


def test_loader_gives_up_instead_of_hanging(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stalled HF download must degrade, not pin the MCP server forever."""
    import time

    from axon.mcp import server

    monkeypatch.setattr(server, "_reranker", None, raising=False)
    monkeypatch.setattr(server, "_RERANK_LOAD_TIMEOUT_S", 0.2)

    class SlowEncoder:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            time.sleep(5)

    monkeypatch.setattr(server, "_text_cross_encoder_cls", lambda: SlowEncoder)
    with pytest.raises(TimeoutError):
        server._get_reranker()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/mcp/test_rerank_contract.py -q -p no:rerunfailures`
Expected: FAIL — `_rerank_hits` returns a list, not a tuple; `_rerank_enabled()` is False.

- [ ] **Step 3: Change the constants and the loader**

```python
# src/axon/mcp/server.py - near line 82
# Measured 2026-08-29 (n=40): 48 candidates x 400 chars scores 0.700 hit rate /
# 0.438 coverage in 4.6s. 1200 chars buys +0.05 for +20s; 256 chars degrades to
# 0.650. See docs/superpowers/specs/2026-08-29-retrieval-pack-quality-design.md.
_RERANK_CANDIDATES = 48
_RERANK_TEXT_CHARS = 400
_RERANK_MODEL = "jinaai/jina-reranker-v2-base-multilingual"
#: Cold-cache load is ~35s on this machine. Past this we give up: a stalled
#: HF download hangs forever and `except Exception` does not catch a hang.
_RERANK_LOAD_TIMEOUT_S = 120.0
```

```python
# src/axon/mcp/server.py - replace _get_reranker
def _text_cross_encoder_cls() -> type:
    """Indirection so tests can substitute the encoder without touching fastembed."""
    try:
        from fastembed import TextCrossEncoder
    except ImportError:
        from fastembed.rerank.cross_encoder import TextCrossEncoder
    return TextCrossEncoder


def _get_reranker() -> object:
    """Load the cross-encoder lazily and with a bound.

    hf_xet's TLS handshake fails on some machines and the download then hangs
    forever rather than raising, so the timeout is the only thing that keeps a
    cold cache from pinning the server. XET is disabled here for the same reason.
    """
    global _reranker
    if _reranker is None:
        import concurrent.futures

        os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
        model_name = os.environ.get("AXON_RERANK_MODEL") or _RERANK_MODEL
        encoder_cls = _text_cross_encoder_cls()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(encoder_cls, model_name)
            try:
                _reranker = future.result(timeout=_RERANK_LOAD_TIMEOUT_S)
            except concurrent.futures.TimeoutError as exc:
                # The worker thread is left running: fastembed offers no cancel,
                # and abandoning it is better than blocking the caller.
                raise TimeoutError(
                    f"reranker load exceeded {_RERANK_LOAD_TIMEOUT_S}s"
                ) from exc
    return _reranker
```

- [ ] **Step 4: Make rerank default-on and report why it did not run**

```python
# src/axon/mcp/server.py - replace _rerank_enabled and _rerank_hits
def _rerank_enabled() -> bool:
    """On by default since 2026-08-29; AXON_RERANK=0 opts out."""
    return os.environ.get("AXON_RERANK", "1") != "0"


def _rerank_hits(query: str, hits: list[dict]) -> tuple[list[dict], str | None]:
    """Reorder hits by cross-encoder score.

    Returns (hits, reason). reason is None when the rerank actually ran; when it
    did not, the ORIGINAL order comes back with a reason, because a pack that
    skipped the rerank must not be indistinguishable from one that passed it.
    """
    if not hits:
        return hits, None
    try:
        docs = [
            str((hit.get("payload") or {}).get("content", ""))[:_RERANK_TEXT_CHARS]
            for hit in hits
        ]
        pairs = [(query, doc) for doc in docs]
        reranker = _get_reranker()
        # fastembed's TextCrossEncoder API: rerank_pairs(pairs). The pinned
        # version (0.8.0) always exposes it; a broken fallback is worse than
        # a clean AttributeError caught by the except below.
        scores = list(reranker.rerank_pairs(pairs))
        scored = [
            (index, float(score), {**hit, "rerank_score": float(score)})
            for index, (hit, score) in enumerate(zip(hits, scores, strict=True))
        ]
        scored.sort(key=lambda item: (-item[1], item[0]))
        return [hit for _, _, hit in scored], None
    except Exception as exc:  # noqa: BLE001 - never break retrieval
        logger.warning("cross-encoder rerank failed; using original order", exc_info=True)
        return hits, f"{type(exc).__name__}: {str(exc)[:120]}"
```

- [ ] **Step 5: Carry the reason into telemetry**

```python
# src/axon/observability/recall_telemetry.py - inside class ChunkRecord, after query
    #: Why the rerank did not run, when it did not. None means it ran (or was
    #: not requested). Without this a skipped rerank and a successful one write
    #: identical rows.
    rerank: str | None = None
```

```python
# src/axon/mcp/server.py - _record_chunk_recall signature and record
def _record_chunk_recall(
    *,
    query: str,
    strategy_name: str,
    requested_max_tokens: int,
    hits: list[dict],
    ctx: str | None = None,
    rerank_note: str | None = None,
) -> None:
```

```python
            strategy=strategy_name,
            requested_max_tokens=requested_max_tokens,
            rerank=rerank_note,
            chunks=chunks,
        )
```

```python
# src/axon/mcp/server.py - inside _retrieve_context
    rerank_note: str | None = None
    if rerank:
        # Reorders whatever candidate list the store returns, hybrid search or not.
        reranked, rerank_note = _rerank_hits(query, results)
        results = _trim_to_budget(
            reranked,
            max_nodes=max_nodes,
            max_tokens=max_tokens,
        )
```

```python
    _record_chunk_recall(
        query=query,
        strategy_name=strategy.name,
        requested_max_tokens=max_tokens,
        hits=telemetry_hits,
        ctx=ctx,
        rerank_note=rerank_note,
    )
```

- [ ] **Step 6: Run the tests**

Run: `python3 -m pytest tests/mcp/ tests/observability/ -q -p no:rerunfailures`
Expected: PASS

- [ ] **Step 7: Run the full suite**

Run: `python3 -m pytest tests/ -q -p no:rerunfailures`
Expected: PASS. `tests/store/test_stores.py` and other Testcontainers tests error
without Docker — that is environmental, not a regression. Compare against a run
on `origin/master` before blaming this task.

- [ ] **Step 8: Measure**

Run:
```bash
AXON_PG_URL=postgresql://axon:axon@localhost:5434/axon HF_HUB_DISABLE_XET=1 \
  python3 scripts/eval_pack_quality.py --top-k 8
AXON_PG_URL=postgresql://axon:axon@localhost:5434/axon \
  python3 scripts/eval_nl_code_recall.py --top-k 5
```
Expected: pack hit rate meaningfully above the Task 1 baseline (target ~0.70,
measured on n=40 during design); symbol recall no worse than 18/24. Put both
numbers in the PR body. A pack-quality gain that comes with a symbol-recall
regression is not a win — report both.

- [ ] **Step 9: Commit**

```bash
git add src/axon/mcp/server.py src/axon/observability/recall_telemetry.py \
        tests/mcp/test_rerank_contract.py
git commit -m "feat(mcp): rerank on by default, with a bounded load and a loud failure"
```

---

## Verification

After all four tasks:

- [ ] `python3 -m pytest tests/ -q -p no:rerunfailures` is green apart from
      Testcontainers errors that also occur on `origin/master`
- [ ] `python3 -m compileall src` succeeds
- [ ] `ruff check` is clean
- [ ] `scripts/eval_pack_quality.py` reports a hit rate above the Task 1 baseline
- [ ] `scripts/eval_nl_code_recall.py` reports at least 18/24
- [ ] `data/recall/chunks.jsonl` contains a row with a non-null `query` and a row
      with `rerank: null` after a successful reranked call
- [ ] No row anywhere in `data/` contains the text of a `ctx=work` query
