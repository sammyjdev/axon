"""Tests for MCP hit path resolution in _build_context_pack."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from axon.core.file_identity import build_repo_roots
from axon.mcp import server


def _strategy() -> SimpleNamespace:
    return SimpleNamespace(
        name="balanced",
        max_segments=8,
        max_chars=4000,
        contexts=("knowledge",),
    )


def test_a_new_row_whose_repo_has_a_root_renders_an_absolute_path_that_exists(
    tmp_path: Path,
) -> None:
    target_file = tmp_path / "repo_a" / "src" / "x.py"
    target_file.parent.mkdir(parents=True, exist_ok=True)
    target_file.write_text("print('hello')", encoding="utf-8")
    roots = build_repo_roots([tmp_path / "repo_a"], tmp_path / "v")

    hit = {
        "score": 0.9,
        "payload": {
            "file_path": "src/x.py",
            "project": "repo_a",
            "symbol": "foo",
            "language": "python",
            "content": "hello world",
        },
    }

    pack = server._build_context_pack(
        strategy=_strategy(),
        task_type="code_analysis",
        profile=None,
        mode="normal",
        effective_ctx="knowledge",
        hits=[hit],
        roots=roots,
    )

    expected_line = f"Arquivo: {target_file.as_posix()}"
    assert expected_line in pack.segments[0]
    extracted_path: Path | None = None
    for line in pack.segments[0].splitlines():
        if line.startswith("Arquivo: "):
            extracted_path = Path(line[len("Arquivo: ") :])
            break
    assert extracted_path is not None
    assert extracted_path.exists()


def test_a_new_row_whose_repo_has_no_root_is_marked_not_on_this_machine() -> None:
    hit = {
        "score": 0.85,
        "payload": {
            "file_path": "src/x.py",
            "project": "pharos",
            "symbol": "foo",
            "language": "python",
            "content": "hello pharos",
        },
    }

    pack = server._build_context_pack(
        strategy=_strategy(),
        task_type="code_analysis",
        profile=None,
        mode="normal",
        effective_ctx="knowledge",
        hits=[hit],
        roots={},
    )

    assert "Arquivo: pharos:src/x.py (not on this machine)" in pack.segments[0]


def test_the_repo_name_is_always_shown_for_a_new_row(tmp_path: Path) -> None:
    roots = build_repo_roots([tmp_path / "repo_a"], tmp_path / "v")
    hit_with_root = {
        "score": 0.9,
        "payload": {
            "file_path": "src/x.py",
            "project": "repo_a",
            "symbol": "foo",
            "language": "python",
            "content": "hello world",
        },
    }
    pack_with_root = server._build_context_pack(
        strategy=_strategy(),
        task_type="code_analysis",
        profile=None,
        mode="normal",
        effective_ctx="knowledge",
        hits=[hit_with_root],
        roots=roots,
    )
    assert "Repo: repo_a" in pack_with_root.segments[0]

    hit_without_root = {
        "score": 0.85,
        "payload": {
            "file_path": "src/x.py",
            "project": "repo_a",
            "symbol": "bar",
            "language": "python",
            "content": "hello outside",
        },
    }
    pack_without_root = server._build_context_pack(
        strategy=_strategy(),
        task_type="code_analysis",
        profile=None,
        mode="normal",
        effective_ctx="knowledge",
        hits=[hit_without_root],
        roots={},
    )
    assert "Repo: repo_a" in pack_without_root.segments[0]


def test_a_legacy_row_still_renders_its_stored_absolute_path(
    tmp_path: Path,
) -> None:
    roots = build_repo_roots([tmp_path / "other" / "axon"], tmp_path / "v")
    hit = {
        "score": 0.8,
        "payload": {
            "file_path": "/Users/sam/dev/axon/src/x.py",
            "project": "axon",
            "symbol": "legacy_fn",
            "language": "python",
            "content": "legacy code",
        },
    }

    pack = server._build_context_pack(
        strategy=_strategy(),
        task_type="code_analysis",
        profile=None,
        mode="normal",
        effective_ctx="knowledge",
        hits=[hit],
        roots=roots,
    )

    assert "Arquivo: /Users/sam/dev/axon/src/x.py" in pack.segments[0]


def test_a_payload_without_a_project_renders_exactly_as_before(
    tmp_path: Path,
) -> None:
    roots = build_repo_roots([tmp_path / "axon"], tmp_path / "v")
    hit = {
        "score": 0.75,
        "payload": {
            "file_path": "/tmp/a.py",  # noqa: S108
            "symbol": "unscoped_fn",
            "language": "python",
            "content": "unscoped code",
        },
    }

    pack = server._build_context_pack(
        strategy=_strategy(),
        task_type="code_analysis",
        profile=None,
        mode="normal",
        effective_ctx="knowledge",
        hits=[hit],
        roots=roots,
    )

    assert "Arquivo: /tmp/a.py" in pack.segments[0]
    assert "Repo:" not in pack.segments[0]


def test_the_default_roots_argument_is_inert() -> None:
    hit = {
        "score": 0.7,
        "payload": {
            "file_path": "src/x.py",
            "project": "repo",
            "symbol": "inert_fn",
            "language": "python",
            "content": "inert code",
        },
    }

    pack = server._build_context_pack(
        strategy=_strategy(),
        task_type="code_analysis",
        profile=None,
        mode="normal",
        effective_ctx="knowledge",
        hits=[hit],
    )

    assert "Arquivo: repo:src/x.py (not on this machine)" in pack.segments[0]
