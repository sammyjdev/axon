"""Tests for the code indexer (T4.1)."""

from __future__ import annotations

import subprocess
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest

from axon.code.indexer import index_file, index_repo
from axon.store.session_store import SessionStore

_PY_SOURCE = """\
def top_level():
    return 1


class Widget:
    def render(self):
        return "ok"
"""


@pytest.fixture
async def store(tmp_path: Path) -> AsyncGenerator[SessionStore, None]:
    s = SessionStore(db_path=tmp_path / "axon.db")
    await s.init()
    yield s
    await s.close()


async def test_index_file_returns_symbols(store: SessionStore, tmp_path: Path) -> None:
    src = tmp_path / "widget.py"
    src.write_text(_PY_SOURCE, encoding="utf-8")

    symbols = await index_file(src, store=store)

    by_id = {s.id: s for s in symbols}
    assert by_id["top_level"].type == "function"
    assert by_id["render"].type == "method"
    assert by_id["render"].language == "python"
    assert by_id["render"].start_line >= 1


async def test_index_file_persists_symbol_nodes(
    store: SessionStore, tmp_path: Path
) -> None:
    src = tmp_path / "widget.py"
    src.write_text(_PY_SOURCE, encoding="utf-8")

    await index_file(src, store=store)

    node = await store.get_node("top_level")
    assert node is not None
    assert node["type"] == "symbol"
    assert node["payload"]["language"] == "python"


async def test_index_file_unsupported_returns_empty(
    store: SessionStore, tmp_path: Path
) -> None:
    doc = tmp_path / "README.md"
    doc.write_text("# hello", encoding="utf-8")
    assert await index_file(doc, store=store) == []


async def test_index_repo_respects_gitignore(
    store: SessionStore, tmp_path: Path
) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "kept.py").write_text("def kept():\n    return 1\n", encoding="utf-8")
    (tmp_path / "ignored.py").write_text(
        "def ignored():\n    return 1\n", encoding="utf-8"
    )
    (tmp_path / ".gitignore").write_text("ignored.py\n", encoding="utf-8")
    # Stage files so git ls-files --cached can see them (iter_git_files uses
    # --cached/tracked-only; untracked files are never returned).
    subprocess.run(
        ["git", "-C", str(tmp_path), "add", "kept.py", ".gitignore"],
        check=True,
        capture_output=True,
    )

    symbols = await index_repo(tmp_path, store=store)

    ids = {s.id for s in symbols}
    assert "kept" in ids
    assert "ignored" not in ids
