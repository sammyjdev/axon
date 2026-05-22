"""Tests for symbol/import edge resolution (T4.2)."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path

import pytest

from axon.code.resolver import index_edges
from axon.store.session_store import SessionStore


@pytest.fixture
async def store(tmp_path: Path) -> AsyncGenerator[SessionStore, None]:
    s = SessionStore(db_path=tmp_path / "axon.db")
    await s.init()
    yield s
    await s.close()


def _make_repo(root: Path) -> None:
    (root / "helpers.py").write_text(
        "def helper():\n    return 1\n", encoding="utf-8"
    )
    (root / "main.py").write_text(
        "from helpers import helper\n\n\ndef run():\n    return helper()\n",
        encoding="utf-8",
    )


async def test_call_edges_link_known_symbols(
    store: SessionStore, tmp_path: Path
) -> None:
    _make_repo(tmp_path)
    edges = await index_edges(tmp_path, store=store)

    calls = {(e.source_id, e.target_id) for e in edges if e.type == "calls"}
    assert ("run", "helper") in calls


async def test_import_edges_link_files(store: SessionStore, tmp_path: Path) -> None:
    _make_repo(tmp_path)
    edges = await index_edges(tmp_path, store=store)

    imports = {(e.source_id, e.target_id) for e in edges if e.type == "imports"}
    assert ("main.py", "helpers.py") in imports


async def test_external_calls_excluded(store: SessionStore, tmp_path: Path) -> None:
    (tmp_path / "solo.py").write_text(
        "def run():\n    return len([1, 2])\n", encoding="utf-8"
    )
    edges = await index_edges(tmp_path, store=store)
    call_targets = {e.target_id for e in edges if e.type == "calls"}
    assert "len" not in call_targets


async def test_edges_are_persisted(store: SessionStore, tmp_path: Path) -> None:
    _make_repo(tmp_path)
    await index_edges(tmp_path, store=store)
    subgraph = await store.query_subgraph("run", depth=1)
    assert "helper" in subgraph["nodes"]
