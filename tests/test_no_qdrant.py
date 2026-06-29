"""Guard: Qdrant is fully retired (dec-121 Phase 1).

Catches the regression class that escaped scoped reviews: a stray qdrant_client
import (e.g. in scripts/) or a lingering qdrant-client dependency. Scoped to
imports + declared deps + deleted-module absence so it does NOT false-positive
on legitimate 'qdrant' keyword/test-data usage.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCAN_DIRS = ("src", "scripts", "tests")
ALLOW_PREFIXES = ("tests/embedder/fixtures/",)  # fixture text parsed by the chunker
_IMPORT = re.compile(r"^\s*(?:from\s+qdrant\S*\s+import|import\s+qdrant)", re.M)


def _py_files():
    for d in SCAN_DIRS:
        for p in (REPO / d).rglob("*.py"):
            rel = p.relative_to(REPO).as_posix()
            if rel.startswith(ALLOW_PREFIXES):
                continue
            yield rel, p


def test_no_qdrant_client_imports_anywhere():
    offenders = [rel for rel, p in _py_files() if _IMPORT.search(p.read_text(encoding="utf-8"))]
    assert not offenders, f"qdrant imports remain (Qdrant retired, dec-121): {offenders}"


def test_qdrant_and_mem0_absent_from_pyproject():
    txt = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    assert "qdrant-client" not in txt, "qdrant-client must be removed from pyproject"
    assert "mem0ai" not in txt, "mem0ai must be removed from pyproject"
    assert "testcontainers[qdrant" not in txt, "testcontainers qdrant extra must be gone"


def test_retired_modules_are_deleted():
    for rel in (
        "src/axon/store/vector_store.py",
        "src/axon/memory/mem0_tool.py",
        "src/axon/memory/config.py",
        "scripts/migrate_bluegreen.py",
        "scripts/verify_migration.py",
    ):
        assert not (REPO / rel).exists(), f"{rel} should be deleted (dec-121 Phase 1)"
