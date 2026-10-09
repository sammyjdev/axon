"""Tests for search path resolution in CLI pb.py."""

from __future__ import annotations

import asyncio
import dataclasses
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from axon.cli import pb
from axon.router.classifier import TaskType

runner = CliRunner()


def test_search_prints_the_resolved_absolute_path_for_a_new_row(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo_a = tmp_path / "repo_a"
    repo_a.mkdir(parents=True, exist_ok=True)
    tmp_engine = tmp_path / "engine"
    data_dir = tmp_engine / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    registry = data_dir / "onboarded_repos.json"
    registry.write_text(json.dumps([str(repo_a)]), encoding="utf-8")

    monkeypatch.setattr(
        pb, "_RUNTIME", dataclasses.replace(pb._RUNTIME, engine_root=tmp_engine)
    )

    hits = [
        {
            "score": 0.95,
            "payload": {
                "file_path": "src/x.py",
                "project": "repo_a",
                "symbol": "run_job",
                "chunk_type": "function",
                "content": "def run_job(): pass",
            },
        }
    ]

    async def fake_hits(*args: object, **kwargs: object) -> list[dict]:
        _ = (args, kwargs)
        await asyncio.sleep(0)
        return hits

    monkeypatch.setattr(pb, "_semantic_search_hits", fake_hits)
    monkeypatch.setattr(
        "axon.router.classifier.classify_task_with_source",
        lambda content, ctx=None: (TaskType.CODE_ANALYSIS, "local"),
    )

    result = runner.invoke(
        pb.app, ["search", "run job", "--ctx", "knowledge", "--top", "1"]
    )
    assert result.exit_code == 0
    expected_path = (repo_a / "src" / "x.py").as_posix()
    assert f"| {expected_path}" in result.stdout


def test_search_prints_the_stored_path_for_a_legacy_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hits = [
        {
            "score": 0.91,
            "payload": {
                "file_path": "/tmp/file_0.py",  # noqa: S108
                "symbol": "legacy_fn",
                "chunk_type": "function",
                "content": "def legacy_fn(): pass",
            },
        }
    ]

    async def fake_hits(*args: object, **kwargs: object) -> list[dict]:
        _ = (args, kwargs)
        await asyncio.sleep(0)
        return hits

    monkeypatch.setattr(pb, "_semantic_search_hits", fake_hits)
    monkeypatch.setattr(
        "axon.router.classifier.classify_task_with_source",
        lambda content, ctx=None: (TaskType.CODE_ANALYSIS, "local"),
    )

    result = runner.invoke(
        pb.app, ["search", "legacy query", "--ctx", "knowledge", "--top", "1"]
    )
    assert result.exit_code == 0
    assert "| /tmp/file_0.py" in result.stdout


def test_search_marks_a_repo_with_no_root_on_this_machine(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    tmp_engine = tmp_path / "engine"
    data_dir = tmp_engine / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    # No registry file written

    monkeypatch.setattr(
        pb, "_RUNTIME", dataclasses.replace(pb._RUNTIME, engine_root=tmp_engine)
    )

    hits = [
        {
            "score": 0.88,
            "payload": {
                "file_path": "src/x.py",
                "project": "repo_a",
                "symbol": "run_job",
                "chunk_type": "function",
                "content": "def run_job(): pass",
            },
        }
    ]

    async def fake_hits(*args: object, **kwargs: object) -> list[dict]:
        _ = (args, kwargs)
        await asyncio.sleep(0)
        return hits

    monkeypatch.setattr(pb, "_semantic_search_hits", fake_hits)
    monkeypatch.setattr(
        "axon.router.classifier.classify_task_with_source",
        lambda content, ctx=None: (TaskType.CODE_ANALYSIS, "local"),
    )

    result = runner.invoke(
        pb.app, ["search", "run job", "--ctx", "knowledge", "--top", "1"]
    )
    assert result.exit_code == 0
    assert "repo_a:src/x.py (not on this machine)" in result.stdout
