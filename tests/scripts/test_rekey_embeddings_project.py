# tests/scripts/test_rekey_embeddings_project.py
"""Tests for scripts/rekey_embeddings_project.py.

Verifies:
- Dry run by default lists candidate rows and changes nothing.
- Refuses --apply without explicit scope flag (exit 2).
- Rejects mutually exclusive scope flags (exit 2).
- With --apply and scope flag, re-keys rows in place deriving project from repo root.
- Post-condition: no project in the embeddings table maps to more than one repo root.
- Clean rows whose project already matches repo root are left untouched.
"""

from __future__ import annotations

import os
import subprocess
from collections import defaultdict
from pathlib import Path

import asyncpg
import pytest

from axon.core.repo_identity import repo_identity
from axon.store.pg_vector_store import VECTOR_SIZE, PgVectorStore
from scripts.rekey_embeddings_project import (
    apply_rekey_embeddings,
    inspect_embeddings,
    main,
    run,
)


def _get_test_pg_url() -> str:
    env = os.environ.get("AXON_PG_URL")
    if env:
        return env
    from axon.config.runtime import load_runtime_config

    return load_runtime_config().pg_url


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(  # noqa: S603
        ["git", *args], cwd=cwd, check=True, capture_output=True  # noqa: S603, S607
    )


def _init_git_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(["init", "-b", "main"], cwd=path)
    _git(["config", "user.email", "test@axon.dev"], cwd=path)
    _git(["config", "user.name", "AXON Test"], cwd=path)
    return path


async def _seed_mixed_embeddings(tmp_path: Path, pg_url: str) -> tuple[Path, Path, Path]:
    repo_a = _init_git_repo(tmp_path / "repo_a")
    repo_b = _init_git_repo(tmp_path / "repo_b")
    repo_clean = _init_git_repo(tmp_path / "repo_clean")

    file_a1 = repo_a / "tests" / "test_x.py"
    file_a2 = repo_a / "tests" / "test_z.py"
    file_b1 = repo_b / "tests" / "test_y.py"
    file_clean = repo_clean / "src" / "mod.py"

    for f in (file_a1, file_a2, file_b1, file_clean):
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("def fn(): pass\n", encoding="utf-8")
        _git(["add", "."], cwd=f.parent)
        _git(["commit", "-m", "init"], cwd=f.parent)

    store = PgVectorStore(pg_url)
    await store.ensure_collections()

    con = await asyncpg.connect(pg_url)
    try:
        await con.execute("TRUNCATE embeddings CASCADE")
        dummy_vec = f"[{','.join(['0.0'] * VECTOR_SIZE)}]"
        await con.execute(
            """
            INSERT INTO embeddings (
                id, vector, ctx, file_path, language, chunk_type, symbol, project, content
            )
            VALUES
            ('emb-1', $1::vector, 'knowledge', $2, 'python',
             'function', 'fn_a1', 'tests', 'def fn(): pass'),
            ('emb-2', $1::vector, 'knowledge', $3, 'python',
             'function', 'fn_b1', 'tests', 'def fn(): pass'),
            ('emb-3', $1::vector, 'knowledge', $4, 'python',
             'function', 'fn_a2', 'tests', 'def fn(): pass'),
            ('emb-clean', $1::vector, 'knowledge', $5, 'python',
             'function', 'fn_clean', 'repo_clean', 'def fn(): pass')
            """,
            dummy_vec,
            str(file_a1),
            str(file_b1),
            str(file_a2),
            str(file_clean),
        )
    finally:
        await con.close()

    return repo_a, repo_b, repo_clean


async def test_dry_run_lists_and_changes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pg_url = _get_test_pg_url()
    await _seed_mixed_embeddings(tmp_path, pg_url)

    exit_code = await run(["--pg-url", pg_url])
    assert exit_code == 0

    out, err = capsys.readouterr()
    assert "would re-key embedding emb-1: tests -> repo_a" in out
    assert "would re-key embedding emb-2: tests -> repo_b" in out
    assert "would re-key embedding emb-3: tests -> repo_a" in out
    assert "tests: 3 row(s)" in out
    assert "would re-key 3 embedding row(s)" in out
    assert "--apply" in out

    # Assert rows in Postgres are untouched
    con = await asyncpg.connect(pg_url)
    try:
        rows = await con.fetch("SELECT id, project FROM embeddings ORDER BY id ASC")
        proj_by_id = {r["id"]: r["project"] for r in rows}
        assert proj_by_id["emb-1"] == "tests"
        assert proj_by_id["emb-2"] == "tests"
        assert proj_by_id["emb-3"] == "tests"
        assert proj_by_id["emb-clean"] == "repo_clean"
    finally:
        await con.close()


async def test_apply_without_scope_flag_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pg_url = _get_test_pg_url()
    await _seed_mixed_embeddings(tmp_path, pg_url)

    exit_code = await run(["--apply", "--pg-url", pg_url])
    assert exit_code == 2

    out, err = capsys.readouterr()
    assert "Refusing to re-key: pass --embeddings or --all with --apply." in err


async def test_mutually_exclusive_scope_flags_refused(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = await run(["--all", "--embeddings"])
    assert exit_code == 2
    out, err = capsys.readouterr()
    assert "--all and --embeddings are mutually exclusive" in err

    exit_code2 = await run(["--all", "--only-project", "tests"])
    assert exit_code2 == 2
    out2, err2 = capsys.readouterr()
    assert "--all and --only-project are mutually exclusive" in err2


async def test_apply_with_scope_rekeys_and_groups_strictly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pg_url = _get_test_pg_url()
    await _seed_mixed_embeddings(tmp_path, pg_url)

    exit_code = await run(["--apply", "--embeddings", "--pg-url", pg_url])
    assert exit_code == 0

    out, err = capsys.readouterr()
    assert "re-keyed embedding emb-1: tests -> repo_a" in out
    assert "re-keyed embedding emb-2: tests -> repo_b" in out
    assert "re-keyed embedding emb-3: tests -> repo_a" in out
    assert "tests: 3 row(s)" in out
    assert "re-keyed 3 embedding row(s)" in out

    # Assert that after the run, no project maps to more than one repo root.
    con = await asyncpg.connect(pg_url)
    try:
        rows = await con.fetch("SELECT id, file_path, project FROM embeddings")
        assert len(rows) == 4

        project_to_roots: dict[str, set[str]] = defaultdict(set)
        for r in rows:
            derived_root = repo_identity(r["file_path"])
            project_to_roots[r["project"]].add(derived_root)

        # The core acceptance assertion: NO project maps to more than one repo root.
        for project_name, roots in project_to_roots.items():
            assert len(roots) == 1, (
                f"Project {project_name!r} maps to multiple repo roots: {roots}"
            )

        assert "tests" not in project_to_roots, "Stale 'tests' project must not remain"
        assert project_to_roots["repo_a"] == {"repo_a"}
        assert project_to_roots["repo_b"] == {"repo_b"}
        assert project_to_roots["repo_clean"] == {"repo_clean"}
    finally:
        await con.close()


async def test_apply_with_only_project_filter(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pg_url = _get_test_pg_url()
    await _seed_mixed_embeddings(tmp_path, pg_url)

    exit_code = await run(
        ["--apply", "--embeddings", "--only-project", "tests", "--pg-url", pg_url]
    )
    assert exit_code == 0

    out, err = capsys.readouterr()
    assert "re-keyed 3 embedding row(s)" in out


async def test_no_matching_rows_reports_cleanly(capsys: pytest.CaptureFixture[str]) -> None:
    pg_url = _get_test_pg_url()
    con = await asyncpg.connect(pg_url)
    try:
        await con.execute("TRUNCATE embeddings CASCADE")
    finally:
        await con.close()

    exit_code = await run(["--pg-url", pg_url])
    assert exit_code == 0

    out, err = capsys.readouterr()
    assert "(no matching embedding rows found)" in out
    assert "would re-key 0 embedding row(s)" in out


def test_main_help_runs_without_error() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0


async def test_credentials_redacted_on_connection_failure(
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "secret_db_password_xyz987"  # noqa: S105
    bad_pg_url = f"postgresql://axon_admin:{secret}@127.0.0.1:1/axon_test"

    with pytest.raises(ConnectionError) as exc_info:
        await inspect_embeddings(bad_pg_url)
    msg = str(exc_info.value)
    assert secret not in msg
    assert "127.0.0.1" in msg

    with pytest.raises(ConnectionError) as exc_info_apply:
        await apply_rekey_embeddings(bad_pg_url)
    msg_apply = str(exc_info_apply.value)
    assert secret not in msg_apply
    assert "127.0.0.1" in msg_apply

    exit_code = await run(["--pg-url", bad_pg_url, "--embeddings"])
    assert exit_code == 1
    _, err = capsys.readouterr()
    assert secret not in err
    assert "127.0.0.1" in err


async def test_apply_with_empty_string_only_project_filter(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pg_url = _get_test_pg_url()
    repo_a = _init_git_repo(tmp_path / "repo_a")
    repo_b = _init_git_repo(tmp_path / "repo_b")

    file_a = repo_a / "tests" / "test_a.py"
    file_b = repo_b / "tests" / "test_b.py"

    for f in (file_a, file_b):
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("def fn(): pass\n", encoding="utf-8")
        _git(["add", "."], cwd=f.parent)
        _git(["commit", "-m", "init"], cwd=f.parent)

    store = PgVectorStore(pg_url)
    await store.ensure_collections()

    con = await asyncpg.connect(pg_url)
    try:
        await con.execute("TRUNCATE embeddings CASCADE")
        dummy_vec = f"[{','.join(['0.0'] * VECTOR_SIZE)}]"
        await con.execute(
            """
            INSERT INTO embeddings (
                id, vector, ctx, file_path, language, chunk_type, symbol, project, content
            )
            VALUES
            ('emb-empty', $1::vector, 'knowledge', $2, 'python',
             'function', 'fn_a', '', 'def fn(): pass'),
            ('emb-stale', $1::vector, 'knowledge', $3, 'python',
             'function', 'fn_b', 'stale_proj', 'def fn(): pass')
            """,
            dummy_vec,
            str(file_a),
            str(file_b),
        )
    finally:
        await con.close()

    # Dry run with empty string filter inspects only emb-empty
    inspect_results = await inspect_embeddings(pg_url, only_project="")
    assert len(inspect_results) == 1
    assert inspect_results[0]["id"] == "emb-empty"

    exit_code = await run(
        ["--apply", "--embeddings", "--only-project", "", "--pg-url", pg_url]
    )
    assert exit_code == 0

    out, _ = capsys.readouterr()
    assert "re-keyed embedding emb-empty:  -> repo_a" in out
    assert "emb-stale" not in out
    assert "re-keyed 1 embedding row(s)" in out

    con = await asyncpg.connect(pg_url)
    try:
        row_empty = await con.fetchrow("SELECT project FROM embeddings WHERE id = 'emb-empty'")
        row_stale = await con.fetchrow("SELECT project FROM embeddings WHERE id = 'emb-stale'")
        assert row_empty is not None and row_empty["project"] == "repo_a"
        # emb-stale must be left untouched because its project is 'stale_proj', not ''
        assert row_stale is not None and row_stale["project"] == "stale_proj"
    finally:
        await con.close()

