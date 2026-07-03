from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from axon.embedder.pipeline import iter_supported_files
from axon.store.file_cache import sha1_of_source


def test_iter_supported_files_skips_dependency_and_build_directories(tmp_path: Path) -> None:
    project = tmp_path / "project"
    src = project / "src"
    src.mkdir(parents=True)
    supported = src / "service.py"
    supported.write_text("def run():\n    return True\n", encoding="utf-8")

    for dirname, filename in [
        ("node_modules", "package.ts"),
        (".git", "hook.py"),
        (".venv", "site.py"),
        ("dist", "bundle.ts"),
        ("target", "Generated.java"),
    ]:
        excluded_dir = project / dirname
        excluded_dir.mkdir(parents=True)
        (excluded_dir / filename).write_text("ignored\n", encoding="utf-8")

    files = list(iter_supported_files(project))

    assert files == [supported]


def test_iter_supported_files_skips_unconventionally_named_virtualenv(tmp_path: Path) -> None:
    # A virtualenv whose directory is not literally ".venv"/"venv" (e.g. a
    # renamed ".venv_hidden", or "py311env") must still be excluded. Every
    # dependency file lives under a "site-packages" segment, so excluding that
    # segment catches the venv regardless of its top-level directory name.
    project = tmp_path / "project"
    src = project / "src"
    src.mkdir(parents=True)
    real = src / "service.py"
    real.write_text("def run():\n    return True\n", encoding="utf-8")

    dep = project / ".venv_hidden" / "lib" / "python3.11" / "site-packages" / "pydantic"
    dep.mkdir(parents=True)
    (dep / "main.py").write_text("class BaseModel:\n    pass\n", encoding="utf-8")

    files = list(iter_supported_files(project))

    assert files == [real]


def test_iter_supported_files_skips_aws_sam_build_artifact(tmp_path: Path) -> None:
    # `sam build` writes bundled Lambda dependencies under ".aws-sam/". The
    # build/ segment catches most of it, but cache and dependency layers can
    # live directly under ".aws-sam/" without a "build" segment, so the
    # ".aws-sam" directory name itself must be excluded — otherwise hundreds of
    # vendored dependency files leak into the index.
    project = tmp_path / "project"
    src = project / "src"
    src.mkdir(parents=True)
    real = src / "handler.py"
    real.write_text("def lambda_handler(event, context):\n    return event\n", encoding="utf-8")

    dep = project / ".aws-sam" / "deps" / "boto3"
    dep.mkdir(parents=True)
    (dep / "client.py").write_text("class Client:\n    pass\n", encoding="utf-8")

    files = list(iter_supported_files(project))

    assert files == [real]


def test_iter_supported_files_applies_language_filter_after_excludes(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    python_file = project / "service.py"
    ts_file = project / "view.ts"
    markdown_file = project / "notes.md"
    python_file.write_text("def run():\n    return True\n", encoding="utf-8")
    ts_file.write_text("export const run = () => true;\n", encoding="utf-8")
    markdown_file.write_text("# Notes\n", encoding="utf-8")

    files = list(iter_supported_files(project, languages={"typescript"}))

    assert files == [ts_file]


class _MockFileCache:
    def __init__(self) -> None:
        self._data: dict[str, str] = {}

    async def get_all_sha1s(self, ctx: str) -> dict[str, str]:
        return dict(self._data)

    async def set_entry(self, file_path, ctx, sha1, chunk_count, *, status="done"):
        self._data[file_path] = sha1

    async def delete_entry(self, file_path, ctx):
        self._data.pop(file_path, None)

    async def list_entries(self, ctx):
        return list(self._data.items())


@pytest.mark.asyncio
async def test_index_path_skips_default_excluded_plan_paths(tmp_path: Path) -> None:
    from axon.embedder.pipeline import index_path

    excluded = tmp_path / "docs" / "superpowers" / "plans" / "plan.md"
    excluded.parent.mkdir(parents=True)
    excluded.write_text("# Plan\n\nShould not be indexed.\n", encoding="utf-8")

    engine = MagicMock()
    engine.embed = MagicMock(return_value=[])
    store = AsyncMock()

    indexed, chunks = await index_path(
        tmp_path,
        engine=engine,
        store=store,
        vault_root=tmp_path,
        file_cache=_MockFileCache(),
    )

    assert indexed == 0
    assert chunks == 0
    engine.embed.assert_not_called()
    store.upsert_batch.assert_not_called()


@pytest.mark.asyncio
async def test_index_path_skips_deep_default_excluded_plan_paths(tmp_path: Path) -> None:
    from axon.embedder.pipeline import index_path

    excluded = tmp_path / "docs" / "superpowers" / "plans" / "archived" / "deep" / "plan.md"
    excluded.parent.mkdir(parents=True)
    excluded.write_text("# Plan\n\nShould not be indexed.\n", encoding="utf-8")

    engine = MagicMock()
    engine.embed = MagicMock(return_value=[])
    store = AsyncMock()

    indexed, chunks = await index_path(
        tmp_path,
        engine=engine,
        store=store,
        vault_root=tmp_path,
        file_cache=_MockFileCache(),
    )

    assert indexed == 0
    assert chunks == 0
    engine.embed.assert_not_called()
    store.upsert_batch.assert_not_called()


@pytest.mark.asyncio
async def test_index_path_indexes_non_excluded_file(tmp_path: Path) -> None:
    from axon.embedder.engine import default_embedding_dimension
    from axon.embedder.pipeline import index_path

    note = tmp_path / "knowledge" / "note.md"
    note.parent.mkdir(parents=True)
    note.write_text("# Note\n\nShould be indexed.\n", encoding="utf-8")

    engine = MagicMock()
    engine.embed = MagicMock(return_value=[[0.1] * default_embedding_dimension()])
    store = AsyncMock()

    indexed, chunks = await index_path(
        tmp_path,
        engine=engine,
        store=store,
        vault_root=tmp_path,
        file_cache=_MockFileCache(),
    )

    assert indexed == 1
    assert chunks == 1
    engine.embed.assert_called()
    store.upsert_batch.assert_called_once()


@pytest.mark.asyncio
async def test_ingest_file_skips_default_excluded_paths(tmp_path: Path) -> None:
    from axon.embedder.pipeline import ingest_file

    excluded = tmp_path / "node_modules" / "pkg" / "index.ts"
    excluded.parent.mkdir(parents=True)
    excluded.write_text("export const ignored = true;\n", encoding="utf-8")

    engine = MagicMock()
    engine.embed = MagicMock(return_value=[])
    store = AsyncMock()

    chunks = await ingest_file(excluded, engine, store)

    assert chunks == 0
    engine.embed.assert_not_called()
    store.upsert_batch.assert_not_called()


@pytest.mark.asyncio
async def test_index_path_env_override_replaces_default_excludes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from axon.embedder.engine import default_embedding_dimension
    from axon.embedder.pipeline import index_path

    monkeypatch.setenv("AXON_INDEX_EXCLUDE", "**/tmp_skip/**")

    default_excluded = tmp_path / "docs" / "superpowers" / "plans" / "plan.md"
    env_excluded = tmp_path / "tmp_skip" / "note.md"
    default_excluded.parent.mkdir(parents=True)
    env_excluded.parent.mkdir(parents=True)
    default_excluded.write_text("# Plan\n\nNow indexed by override.\n", encoding="utf-8")
    env_excluded.write_text("# Skip\n\nIgnored by override.\n", encoding="utf-8")

    engine = MagicMock()
    engine.embed = MagicMock(return_value=[[0.1] * default_embedding_dimension()])
    store = AsyncMock()
    cache = _MockFileCache()

    indexed, chunks = await index_path(
        tmp_path,
        engine=engine,
        store=store,
        vault_root=tmp_path,
        file_cache=cache,
    )

    assert indexed == 1
    assert chunks == 1
    assert cache._data == {
        default_excluded.as_posix(): sha1_of_source(default_excluded.read_text())
    }


def test_empty_env_override_uses_default_excludes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AXON_INDEX_EXCLUDE", "  ")

    excluded = tmp_path / "docs" / "superpowers" / "plans" / "plan.md"
    included = tmp_path / "notes.md"
    excluded.parent.mkdir(parents=True)
    excluded.write_text("# Plan\n", encoding="utf-8")
    included.write_text("# Note\n", encoding="utf-8")

    assert list(iter_supported_files(tmp_path)) == [included]
