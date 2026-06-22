# pgvector Cutover Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make pgvector the active vector backend, selected by an `axon.toml` setting (env-overridable), after validating a real-vault re-index at recall parity with Qdrant.

**Architecture:** Add `RuntimeConfig.vector_backend` sourced from `axon.toml [runtime] vector_backend` with `AXON_VECTOR_BACKEND` overriding. `make_vector_store()` reads that field instead of the env var directly. `doctor` surfaces the active backend. A parity script + runbook support the operator cutover. The default flips to `pgvector` only in the final task, gated on the acceptance checks. Production index/search are already backend-agnostic, so no production code path changes.

**Tech Stack:** Python 3.11+, frozen `@dataclass` `RuntimeConfig`, tomllib via the existing `_load_toml_runtime_overrides`, typer CLI, pytest.

## Global Constraints

- Backend selection precedence is exactly: `AXON_VECTOR_BACKEND` env (if set, non-empty) > `axon.toml [runtime] vector_backend` > the default.
- `vector_backend` is constrained to `{"qdrant", "pgvector"}`; an unknown value raises `ValueError` at config load (no silent fallback).
- The runtime default stays `"qdrant"` until Task 5 (the gated cutover) flips it to `"pgvector"`. Do NOT flip earlier.
- `RuntimeConfig.vector_backend` is a DEFAULTED trailing field (`= "qdrant"`) so existing manual `RuntimeConfig(...)` constructions do not break (they did in step 1 when `pg_url` was added as required).
- Callers of `make_vector_store()` are untouched.
- Only plain hyphens `-` in code/comments/docs, never em or en dashes.
- No model load or live backend in unit tests.

---

### Task 1: `vector_backend` on RuntimeConfig + precedence resolver

**Files:**
- Modify: `src/axon/config/runtime.py` (add field, `_resolve_vector_backend`, wire into `load_runtime_config`)
- Test: `tests/config/test_vector_backend.py`

**Interfaces:**
- Consumes: `load_runtime_config()`, `_load_toml_runtime_overrides()` (returns the `[runtime]` dict).
- Produces: `RuntimeConfig.vector_backend: str`; `_resolve_vector_backend(overrides: dict) -> str` with env > toml > default precedence and `{"qdrant","pgvector"}` validation.

- [ ] **Step 1: Write the failing test**

```python
# tests/config/test_vector_backend.py
from __future__ import annotations

import pytest


def test_vector_backend_defaults_to_qdrant(monkeypatch) -> None:
    monkeypatch.delenv("AXON_VECTOR_BACKEND", raising=False)
    from axon.config.runtime import load_runtime_config

    assert load_runtime_config().vector_backend == "qdrant"


def test_vector_backend_env_overrides(monkeypatch) -> None:
    monkeypatch.setenv("AXON_VECTOR_BACKEND", "pgvector")
    from axon.config.runtime import load_runtime_config

    assert load_runtime_config().vector_backend == "pgvector"


def test_vector_backend_rejects_unknown(monkeypatch) -> None:
    monkeypatch.setenv("AXON_VECTOR_BACKEND", "weaviate")
    from axon.config.runtime import load_runtime_config

    with pytest.raises(ValueError):
        load_runtime_config()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/config/test_vector_backend.py -v`
Expected: FAIL (`RuntimeConfig` has no attribute `vector_backend`).

- [ ] **Step 3: Add the field, resolver, and wiring**

In `src/axon/config/runtime.py`:

Add a defaulted trailing field on `RuntimeConfig` (next to the existing defaulted `active_profile = None`, so frozen-dataclass field ordering stays valid):

```python
    vector_backend: str = "qdrant"
```

Add a module-level resolver (near the other `_resolve_*` / `_env_*` helpers):

```python
_VALID_VECTOR_BACKENDS = ("qdrant", "pgvector")


def _resolve_vector_backend(overrides: dict) -> str:
    """Select the vector backend: AXON_VECTOR_BACKEND env > axon.toml > default."""
    raw = os.environ.get("AXON_VECTOR_BACKEND") or overrides.get("vector_backend") or "qdrant"
    backend = raw.strip().lower()
    if backend not in _VALID_VECTOR_BACKENDS:
        raise ValueError(
            f"Invalid vector_backend {backend!r}; expected one of {list(_VALID_VECTOR_BACKENDS)}"
        )
    return backend
```

In `load_runtime_config()`, pass it in the `RuntimeConfig(...)` construction (the function already has `overrides` from `_load_toml_runtime_overrides()`; add the kwarg alongside `pg_url=`):

```python
        vector_backend=_resolve_vector_backend(overrides),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/config/test_vector_backend.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/axon/config/runtime.py tests/config/test_vector_backend.py
git commit -m "feat(pgvector): RuntimeConfig.vector_backend (env > axon.toml > default), validated"
```

---

### Task 2: `make_vector_store()` selects by `runtime.vector_backend`

**Files:**
- Modify: `src/axon/store/vector_store_factory.py`
- Test: `tests/store/test_vector_store_factory.py`

**Interfaces:**
- Consumes: `RuntimeConfig.vector_backend` (Task 1).
- Produces: `make_vector_store(runtime=None)` selecting on `rt.vector_backend` (env override still flows through `load_runtime_config`).

- [ ] **Step 1: Write the failing test**

```python
# add to tests/store/test_vector_store_factory.py
def test_backend_from_runtime_vector_backend(monkeypatch) -> None:
    monkeypatch.delenv("AXON_VECTOR_BACKEND", raising=False)
    from axon.config.runtime import load_runtime_config
    from axon.store.pg_vector_store import PgVectorStore
    from axon.store.vector_store_factory import make_vector_store

    rt = load_runtime_config()
    # construct a runtime explicitly on pgvector (frozen dataclass -> use replace)
    import dataclasses

    rt_pg = dataclasses.replace(rt, vector_backend="pgvector")
    assert isinstance(make_vector_store(rt_pg), PgVectorStore)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/store/test_vector_store_factory.py::test_backend_from_runtime_vector_backend -v`
Expected: FAIL (factory still reads the env var, ignores `rt.vector_backend`).

- [ ] **Step 3: Change the backend-selection line**

In `src/axon/store/vector_store_factory.py`, replace:

```python
    backend = os.environ.get("AXON_VECTOR_BACKEND", "qdrant").strip().lower()
```

with:

```python
    backend = rt.vector_backend
```

The `import os` may become unused; remove it if so. The rest (the `if backend == "pgvector": return PgVectorStore(dsn=rt.pg_url)` / else `VectorStore(url=rt.qdrant_url)`) is unchanged.

- [ ] **Step 4: Run tests to verify pass + no regression**

Run: `.venv/Scripts/python.exe -m pytest tests/store/test_vector_store_factory.py -v`
Expected: all pass - `test_default_is_qdrant` (no env -> default qdrant), `test_pgvector_selected_by_env` (env flows through `load_runtime_config`), and the new runtime-driven test.

- [ ] **Step 5: Commit**

```bash
git add src/axon/store/vector_store_factory.py tests/store/test_vector_store_factory.py
git commit -m "feat(pgvector): make_vector_store selects by runtime.vector_backend (config-driven)"
```

---

### Task 3: doctor surfaces the active vector backend

**Files:**
- Modify: `src/axon/cli/pb.py` (the `doctor` command, around line 851)
- Test: `tests/cli/test_pb_cli.py` (the existing `test_doctor_prints_recommended_mode_and_checks`)

**Interfaces:**
- Consumes: `runtime.vector_backend` (already loaded as `runtime` in `doctor`).
- Produces: a `vector_backend: <backend>` line in `pb doctor` output.

- [ ] **Step 1: Write the failing assertion**

In `tests/cli/test_pb_cli.py`, add to `test_doctor_prints_recommended_mode_and_checks` (after the existing assertions on the result output):

```python
    assert "vector_backend:" in result.stdout
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/cli/test_pb_cli.py::test_doctor_prints_recommended_mode_and_checks -v`
Expected: FAIL (no `vector_backend:` line yet).

- [ ] **Step 3: Add the line**

In `src/axon/cli/pb.py`, immediately after the `recommended_mode` echo (line ~851):

```python
    typer.echo(f"vector_backend: {runtime.vector_backend}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/cli/test_pb_cli.py::test_doctor_prints_recommended_mode_and_checks -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/axon/cli/pb.py tests/cli/test_pb_cli.py
git commit -m "feat(pgvector): doctor surfaces active vector_backend"
```

---

### Task 4: parity check + cutover runbook

**Files:**
- Create or modify: `scripts/verify_migration.py` (add a pgvector-vs-Qdrant parity mode)
- Create: `docs/MIGRATION.md` section (or file) - the cutover runbook
- Test: `tests/scripts/test_verify_migration_parity.py`

**Interfaces:**
- Produces: a pure helper `counts_match(qdrant_count: int, pg_count: int) -> bool` and a `parity_summary(per_ctx: dict[str, tuple[int, int]]) -> tuple[bool, str]` used by the script; the live-backend wiring calls them.

- [ ] **Step 1: Write the failing test (pure logic, no live backend)**

```python
# tests/scripts/test_verify_migration_parity.py
from __future__ import annotations


def test_counts_match_exact() -> None:
    from scripts.verify_migration import counts_match

    assert counts_match(120, 120) is True
    assert counts_match(120, 119) is False


def test_parity_summary_reports_per_ctx() -> None:
    from scripts.verify_migration import parity_summary

    ok, text = parity_summary({"personal": (120, 120), "work": (5, 4)})
    assert ok is False
    assert "personal" in text and "work" in text
    assert "FAIL" in text
```

(If `scripts/` is not importable in tests, add an empty `scripts/__init__.py` and ensure `tests/scripts/__init__.py` exists; the repo already puts the repo root on `pythonpath`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/scripts/test_verify_migration_parity.py -v`
Expected: FAIL (helpers not defined).

- [ ] **Step 3: Implement the helpers + script wiring**

Add to `scripts/verify_migration.py` (create the file if absent, mirroring `scripts/migrate_bluegreen.py` argparse style):

```python
def counts_match(qdrant_count: int, pg_count: int) -> bool:
    """Exact row/point-count parity for one ctx."""
    return qdrant_count == pg_count


def parity_summary(per_ctx: dict[str, tuple[int, int]]) -> tuple[bool, str]:
    """Summarize per-ctx (qdrant_count, pg_count) parity. Returns (all_ok, text)."""
    lines = []
    all_ok = True
    for ctx, (qn, pn) in sorted(per_ctx.items()):
        ok = counts_match(qn, pn)
        all_ok = all_ok and ok
        lines.append(f"  {ctx}: qdrant={qn} pgvector={pn} -> {'OK' if ok else 'FAIL'}")
    verdict = "PASS" if all_ok else "FAIL"
    return all_ok, f"parity [{verdict}]\n" + "\n".join(lines)
```

The live-backend `main()` (guarded by `if __name__ == "__main__"`) reads the Qdrant point count per ctx and the pgvector `SELECT count(*) FROM embeddings WHERE ctx=$1`, builds `per_ctx`, calls `parity_summary`, prints it, and exits non-zero on FAIL. Keep the model out of this script (counts + a few `search` spot-checks only).

- [ ] **Step 4: Write the runbook**

Add `docs/MIGRATION.md` (cutover section): the sequence (bring up `axon-postgres`; `AXON_VECTOR_BACKEND=pgvector axon index <vault> --ctx <ctx>` per ctx with data; `python scripts/verify_migration.py` parity; `AXON_VECTOR_BACKEND=pgvector AXON_RUN_RECALL=1` recall gate; flip `vector_backend = "pgvector"` in `axon.toml`; confirm `axon pb doctor` shows `vector_backend: pgvector`), the rollback (set `vector_backend = "qdrant"`; Qdrant data intact), and a note that Qdrant is retired only in dec-121 step 5. Plain hyphens only.

- [ ] **Step 5: Run test to verify it passes + commit**

Run: `.venv/Scripts/python.exe -m pytest tests/scripts/test_verify_migration_parity.py -v`
Expected: 2 passed.

```bash
git add scripts/verify_migration.py docs/MIGRATION.md tests/scripts/test_verify_migration_parity.py
git commit -m "feat(pgvector): cutover parity check + migration runbook"
```

---

### Task 5: Cutover - acceptance gate + flip the default (controller-run)

**Files:**
- Modify: `src/axon/config/runtime.py` (`_resolve_vector_backend` default)
- Test: `tests/config/test_vector_backend.py`, `tests/store/test_vector_store_factory.py`

This task is GATED on operator-run acceptance checks that need the real vault + GPU (not autonomous-agent runnable). Do the validation FIRST, only then flip.

- [ ] **Step 1: Acceptance gate (operator-run, must all pass before flipping)**

```bash
docker compose up -d axon-postgres
AXON_VECTOR_BACKEND=pgvector .venv/Scripts/python.exe -m axon.cli.pb index <vault> --ctx personal   # clean, non-zero rows
python scripts/verify_migration.py                                                                   # parity PASS
AXON_VECTOR_BACKEND=pgvector AXON_RUN_RECALL=1 .venv/Scripts/python.exe -m pytest tests/recall/test_recall_guard.py::test_recall_guard_no_regression -q
```
Proceed to Step 2 ONLY if all three pass. If any fails, STOP and investigate - do not flip.

- [ ] **Step 2: Update the default tests (RED for the flip)**

In `tests/config/test_vector_backend.py`, change `test_vector_backend_defaults_to_qdrant` to expect pgvector and rename:

```python
def test_vector_backend_defaults_to_pgvector(monkeypatch) -> None:
    monkeypatch.delenv("AXON_VECTOR_BACKEND", raising=False)
    from axon.config.runtime import load_runtime_config

    assert load_runtime_config().vector_backend == "pgvector"
```

In `tests/store/test_vector_store_factory.py`, change `test_default_is_qdrant` to expect pgvector and rename to `test_default_is_pgvector` (no env -> default now pgvector):

```python
def test_default_is_pgvector(monkeypatch) -> None:
    monkeypatch.delenv("AXON_VECTOR_BACKEND", raising=False)
    from axon.store.pg_vector_store import PgVectorStore
    from axon.store.vector_store_factory import make_vector_store

    assert isinstance(make_vector_store(), PgVectorStore)
```

- [ ] **Step 3: Run to verify they FAIL (default still qdrant)**

Run: `.venv/Scripts/python.exe -m pytest tests/config/test_vector_backend.py::test_vector_backend_defaults_to_pgvector tests/store/test_vector_store_factory.py::test_default_is_pgvector -v`
Expected: FAIL (default is still qdrant).

- [ ] **Step 4: Flip the default**

In `src/axon/config/runtime.py`, in `_resolve_vector_backend`, change the fallback:

```python
    raw = os.environ.get("AXON_VECTOR_BACKEND") or overrides.get("vector_backend") or "pgvector"
```

(Leave the `RuntimeConfig.vector_backend = "qdrant"` field default as the fixture fallback - manual test constructions that do not exercise backend selection stay on qdrant and are unaffected.)

- [ ] **Step 5: Run to verify pass + full non-GPU sweep**

Run: `.venv/Scripts/python.exe -m pytest tests/config/test_vector_backend.py tests/store/test_vector_store_factory.py tests/cli/test_pb_cli.py -q -p no:cacheprovider`
Expected: green (default now pgvector; env override to qdrant still works for rollback).

- [ ] **Step 6: Commit**

```bash
git add src/axon/config/runtime.py tests/config/test_vector_backend.py tests/store/test_vector_store_factory.py
git commit -m "feat(pgvector): cutover - default vector backend is now pgvector (Qdrant via override/rollback)"
```

---

## Notes for the executor

- Tasks 1-4 are autonomous (config, factory, doctor, tooling/docs). Task 5's Step 1 acceptance gate needs the real vault + GPU and is operator-run; the flip (Steps 2-6) follows only after it passes.
- The default flip is deliberately the LAST change. Until Task 5, `vector_backend` defaults to qdrant so nothing in CI or other branches silently switches backends.
- Do not change any `make_vector_store()` caller; selection stays entirely in config + factory.
- Qdrant stays present and functional as the rollback target. Its removal is dec-121 step 5, not here.
