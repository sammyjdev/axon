"""Guard: Redis is fully retired (dec-121 Phase 2).

The dep-graph moved to a Postgres ``symbol_deps`` table and the resilience layer
(rate limiter / circuit breaker) is in-memory only, so no module should import
redis and no redis dependency should remain. Mirrors tests/test_no_qdrant.py.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCAN_DIRS = ("src", "scripts", "tests")
ALLOW_PREFIXES = ("tests/embedder/fixtures/",)  # fixture text parsed by the chunker
_IMPORT = re.compile(r"^\s*(?:from\s+redis\S*\s+import|import\s+redis)", re.M)


def _py_files():
    for d in SCAN_DIRS:
        for p in (REPO / d).rglob("*.py"):
            rel = p.relative_to(REPO).as_posix()
            if rel.startswith(ALLOW_PREFIXES):
                continue
            yield rel, p


def test_no_redis_imports_anywhere():
    offenders = [rel for rel, p in _py_files() if _IMPORT.search(p.read_text(encoding="utf-8"))]
    assert not offenders, f"redis imports remain (Redis retired, dec-121 Phase 2): {offenders}"


def test_redis_absent_from_pyproject():
    txt = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(r'^\s*"redis[>=~]', txt, re.M) is None, "redis dep must be removed"
    assert "testcontainers[redis" not in txt, "testcontainers redis extra must be gone"


def test_graph_store_module_deleted():
    assert not (REPO / "src/axon/store/graph_store.py").exists()
