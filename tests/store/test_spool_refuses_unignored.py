"""The spool writes nothing git could stage (axon #202).

A payload may land only in a directory git provably ignores, or outside any
git work tree. Every repository here is a real temp repo; the suite's autouse
fixture isolates the global git config, so nothing is ignored by accident.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from axon.store import pending
from axon.store.pending import _pending_paths, write_pending
from tests.cli.test_spool_not_committable import _git

_PAYLOAD = {"kind": "session_memory", "summary": "literal user prompt"}


def _repo(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("AXON_DATA_ROOT", str(tmp_path / ".axon"))
    _git(tmp_path, "init")
    return tmp_path


async def _write(commit_hash: str = "session") -> Path:
    return await write_pending(payload=_PAYLOAD, commit_hash=commit_hash, paths=_pending_paths())


def _read_only_replace(*_a, **_kw):
    raise OSError(30, "Read-only file system")


async def test_write_pending_refuses_when_ignore_write_fails(tmp_path, monkeypatch):
    """Criterion 2: the rule cannot be written and git does not ignore the spool."""
    repo = _repo(tmp_path, monkeypatch)
    monkeypatch.setattr("axon.store.pending.os.replace", _read_only_replace)

    with pytest.raises(pending.SpoolNotIgnoredError) as refused:
        await _write()

    assert "OSError: [Errno 30] Read-only file system" in str(refused.value)
    assert str(refused.value).startswith(f"spool not ignored: {repo / '.axon' / 'pending'}: ")
    assert refused.value.directory == repo / ".axon" / "pending"
    assert str(refused.value).endswith(refused.value.reason)
    assert list((repo / ".axon" / "pending").glob("*.json")) == []
    assert list((repo / ".axon").glob(".gitignore.*.tmp")) == []


@pytest.mark.parametrize("case", ["axon_is_a_file", "git_timeout"])
async def test_unclassified_rev_parse_result_refuses(tmp_path, monkeypatch, case):
    """Criterion 12: any rev-parse answer that is not a plain yes or no refuses."""
    repo = _repo(tmp_path, monkeypatch)
    if case == "axon_is_a_file":
        (repo / ".axon").write_text("not a directory", encoding="utf-8")
        expected = "FileExistsError"
    else:

        def timeout(cmd, **_kw):
            raise subprocess.TimeoutExpired(cmd, 10)

        monkeypatch.setattr(subprocess, "run", timeout)
        expected = "git timed out after 10s"

    with pytest.raises(pending.SpoolNotIgnoredError) as refused:
        await _write()

    assert expected in str(refused.value)
    assert list(repo.rglob("*.json")) == []


async def test_refuses_when_git_is_missing(tmp_path, monkeypatch):
    """Criterion 11: a guard that cannot run must not report success."""
    repo = _repo(tmp_path, monkeypatch)

    def missing(*_a, **_kw):
        raise FileNotFoundError(2, "No such file or directory", "git")

    monkeypatch.setattr(subprocess, "run", missing)

    with pytest.raises(pending.SpoolNotIgnoredError) as refused:
        pending._ensure_spool_ignored(_pending_paths())

    assert "git not found" in str(refused.value)
    with pytest.raises(pending.SpoolNotIgnoredError):
        await _write()
    assert list(repo.rglob("*.json")) == []


def _commit_tracked_spool_file(repo: Path) -> None:
    spool = repo / ".axon" / "pending"
    spool.mkdir(parents=True)
    (repo / ".axon" / ".gitignore").write_text("/pending/\n", encoding="utf-8")
    (spool / "old.json").write_text("{}", encoding="utf-8")
    _git(repo, "add", "-f", ".axon/pending/old.json")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "old")


def _data_root_form(repo: Path, monkeypatch, form: str) -> None:
    """`relative` is the default: AXON_DATA_ROOT unset, cwd the repo."""
    if form == "relative":
        monkeypatch.delenv("AXON_DATA_ROOT")
        monkeypatch.chdir(repo)


@pytest.mark.parametrize("form", ["absolute", "relative"])
async def test_write_pending_refuses_when_a_spool_file_is_tracked(tmp_path, monkeypatch, form):
    """Criterion 4: ignore rules do not apply to a file git already tracks."""
    repo = _repo(tmp_path, monkeypatch)
    _commit_tracked_spool_file(repo)
    _data_root_form(repo, monkeypatch, form)

    with pytest.raises(pending.SpoolNotIgnoredError) as refused:
        await _write()

    assert "tracked" in str(refused.value)
    assert [p.name for p in (repo / ".axon" / "pending").iterdir()] == ["old.json"]


@pytest.mark.parametrize("form", ["absolute", "relative"])
async def test_quarantine_refuses_when_check_ignore_fails(tmp_path, monkeypatch, form):
    """Criterion 5: the rules are present but a later negation undoes them."""
    repo = _repo(tmp_path, monkeypatch)
    (repo / ".axon" / "pending").mkdir(parents=True)
    (repo / ".axon" / ".gitignore").write_text(
        "pending/\n!pending/\npending-quarantine/\n!pending-quarantine/\n", encoding="utf-8"
    )
    _data_root_form(repo, monkeypatch, form)
    paths = _pending_paths()
    source = paths.pending_dir / "bad.json"
    source.write_text("{not json", encoding="utf-8")

    with pytest.raises(pending.SpoolNotIgnoredError):
        await pending.quarantine_invalid(source, reason="bad", paths=paths)

    assert source.exists(), "the payload was moved into a directory git can stage"
    assert list((repo / ".axon").glob("pending-quarantine/*")) == []


async def test_inherited_git_dir_does_not_fail_open(tmp_path, monkeypatch):
    """Git hooks export GIT_DIR; a relative one would make rev-parse answer
    "not a git repository" from the data root and read as outside."""
    repo = _repo(tmp_path, monkeypatch)
    _commit_tracked_spool_file(repo)
    monkeypatch.setenv("GIT_DIR", ".git")

    with pytest.raises(pending.SpoolNotIgnoredError) as refused:
        await _write()

    assert "tracked" in str(refused.value)
    assert [p.name for p in (repo / ".axon" / "pending").iterdir()] == ["old.json"]
