"""Guard: SQLite is fully retired (dec-121 Phase 3).

The relational source-of-truth (decisions/ADRs/sessions/graph/file_index) plus
the FailureStore/OutcomeStore all moved to Postgres, so no module should import
aiosqlite or sqlite3 and no aiosqlite dependency should remain. Mirrors
tests/test_no_qdrant.py / tests/test_no_redis.py.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCAN_DIRS = ("src", "scripts", "tests")
ALLOW_PREFIXES = ("tests/embedder/fixtures/",)  # fixture text parsed by the chunker
_IMPORT = re.compile(
    r"^\s*(?:from\s+(?:aiosqlite|sqlite3)\b|import\s+(?:aiosqlite|sqlite3)\b)", re.M
)


def _py_files():
    for d in SCAN_DIRS:
        for p in (REPO / d).rglob("*.py"):
            rel = p.relative_to(REPO).as_posix()
            if rel.startswith(ALLOW_PREFIXES):
                continue
            yield rel, p


def test_no_sqlite_imports_anywhere():
    offenders = [rel for rel, p in _py_files() if _IMPORT.search(p.read_text(encoding="utf-8"))]
    assert not offenders, f"sqlite imports remain (SQLite retired, dec-121 Phase 3): {offenders}"


def test_aiosqlite_absent_from_pyproject():
    txt = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(r'^\s*"aiosqlite[>=~]', txt, re.M) is None, "aiosqlite dep must be removed"


def test_sqlite_repo_modules_deleted():
    for rel in (
        "src/axon/store/session_repository.py",
        "src/axon/store/decision_repository.py",
        "src/axon/store/graph_repository.py",
        "src/axon/store/sqlite_helpers.py",
        "src/axon/store/decision_backfill.py",
    ):
        assert not (REPO / rel).exists(), f"{rel} should be deleted (dec-121 Phase 3)"
