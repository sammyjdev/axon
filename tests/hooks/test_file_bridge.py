"""Tests for the .axon/context.md file bridge (T3.4)."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from axon.core.decision import Decision
from axon.hooks.file_bridge import update_context_file
from axon.store.session_store import SessionStore


@pytest.fixture
async def store(tmp_path: Path) -> AsyncGenerator[SessionStore, None]:
    s = SessionStore(db_path=tmp_path / "axon.db")
    await s.init()
    yield s
    await s.close()


def _decision(**overrides: Any) -> Decision:
    base: dict[str, Any] = dict(
        id="dec-001",
        timestamp=datetime(2026, 5, 1, tzinfo=UTC),
        agent="claude-code",
        repo="myrepo",
        summary="a decision",
    )
    base.update(overrides)
    return Decision(**base)


async def test_writes_context_file_with_decisions(
    store: SessionStore, tmp_path: Path
) -> None:
    repo_root = tmp_path / "myrepo"
    repo_root.mkdir()
    await store.save_decision(
        _decision(
            id="dec-001",
            summary="rename to axon",
            symbols=["pkg.Mod.Cls"],
            status="active",
        )
    )

    target = await update_context_file(repo_root, store=store)

    assert target == repo_root / ".axon" / "context.md"
    text = target.read_text(encoding="utf-8")
    assert "# AXON context — myrepo" in text
    assert "dec-001" in text and "rename to axon" in text
    assert "Active symbols" not in text and "pkg.Mod.Cls" not in text


async def test_empty_repo_renders_placeholders(
    store: SessionStore, tmp_path: Path
) -> None:
    repo_root = tmp_path / "myrepo"
    repo_root.mkdir()
    target = await update_context_file(repo_root, store=store)
    text = target.read_text(encoding="utf-8")
    assert "No active decisions." in text
    assert "axon adr list" not in text


async def test_write_is_atomic_no_tmp_left(
    store: SessionStore, tmp_path: Path
) -> None:
    repo_root = tmp_path / "myrepo"
    repo_root.mkdir()
    await update_context_file(repo_root, store=store)
    assert not (repo_root / ".axon" / "context.md.tmp").exists()
    assert (repo_root / ".axon" / "context.md").exists()


async def test_rewrite_refreshes_content(
    store: SessionStore, tmp_path: Path
) -> None:
    repo_root = tmp_path / "myrepo"
    repo_root.mkdir()
    await update_context_file(repo_root, store=store)
    await store.save_decision(
        _decision(id="dec-001", summary="now there is one", status="active")
    )
    target = await update_context_file(repo_root, store=store)
    assert "now there is one" in target.read_text(encoding="utf-8")


async def test_rewrite_is_byte_identical_when_the_store_is_unchanged(
    store: SessionStore, tmp_path: Path
) -> None:
    repo_root = tmp_path / "myrepo"
    repo_root.mkdir()
    await store.save_decision(_decision(id="dec-001", summary="stable", status="active"))

    first = await update_context_file(repo_root, store=store)
    first_bytes = first.read_bytes()
    second = await update_context_file(repo_root, store=store)

    assert second.read_bytes() == first_bytes
    assert b"_Updated" not in first_bytes
