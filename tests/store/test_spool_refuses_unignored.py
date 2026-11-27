"""The spool writes nothing git could stage (axon #202).

A payload may land only in a directory git provably ignores, or outside any
git work tree. Every repository here is a real temp repo; the suite's autouse
fixture isolates the global git config, so nothing is ignored by accident.
"""

from __future__ import annotations

import os
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


@pytest.mark.parametrize("case", ["tmp_dir", "under_dot_git"])
async def test_write_pending_outside_a_work_tree_writes(tmp_path, monkeypatch, case):
    """Criterion 6: where git can stage nothing, spooling works as before."""
    if case == "under_dot_git":
        _git(tmp_path, "init")
        monkeypatch.setenv("AXON_DATA_ROOT", str(tmp_path / ".git" / "axon"))
    else:
        monkeypatch.setenv("AXON_DATA_ROOT", str(tmp_path / ".axon"))

    written = await _write()

    assert written.is_file()
    assert len(list(written.parent.glob("*.json"))) == 1


async def test_partial_name_negation_is_refused(tmp_path, monkeypatch):
    """Criterion 13: the probe is ignored, the payload's real name is not."""
    repo = _repo(tmp_path, monkeypatch)
    (repo / ".axon").mkdir()
    (repo / ".axon" / ".gitignore").write_text(
        "pending/*\n!pending/sess-*.json\n", encoding="utf-8"
    )

    with pytest.raises(pending.SpoolNotIgnoredError):
        await _write("sess")

    porcelain = _git(repo, "status", "--porcelain", "--untracked-files=all")
    assert ".axon/pending/" not in porcelain


async def test_write_error_with_proven_ignore_writes_and_reports(tmp_path, monkeypatch, capsys):
    """Criterion 14: the write failed, but git's answer is what decides."""
    repo = _repo(tmp_path, monkeypatch)
    (repo / ".gitignore").write_text(".axon/\n", encoding="utf-8")

    real_replace = os.replace

    def refuse_the_ignore_file(src, dst):
        if Path(dst).name == ".gitignore":
            _read_only_replace()
        real_replace(src, dst)

    monkeypatch.setattr("axon.store.pending.os.replace", refuse_the_ignore_file)

    written = await _write()

    assert written.is_file()
    assert len(list(written.parent.glob("*.json"))) == 1
    lines = [
        line
        for line in capsys.readouterr().err.splitlines()
        if line.startswith("[axon] spool ignore rule not written (OSError:")
    ]
    assert len(lines) == 1
    assert lines[0].endswith(f"git already ignores {repo / '.axon' / 'pending'}")


class _Killed(BaseException):
    """The process dies midway. KeyboardInterrupt would stop pytest itself."""


def _stageable(repo: Path) -> str:
    return _git(repo, "status", "--porcelain", "--untracked-files=all", "--", ".axon/pending")


def _refuse_replace_onto(monkeypatch, name: str) -> None:
    real_replace = os.replace

    def replace(src, dst):
        if Path(dst).name == name:
            _read_only_replace()
        real_replace(src, dst)

    monkeypatch.setattr("axon.store.pending.os.replace", replace)


def _die_midway(monkeypatch, error: BaseException) -> None:
    """The payload's temp write leaves half a file and raises."""
    real_write_text = Path.write_text

    def write_text(self, data, *args, **kwargs):
        if self.name.endswith(".json.tmp"):
            real_write_text(self, data[: len(data) // 2], *args, **kwargs)
            raise error
        return real_write_text(self, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", write_text)


@pytest.mark.parametrize("case", ["negation", "root_json_rule"])
async def test_write_pending_refuses_when_the_temp_name_is_not_ignored(
    tmp_path, monkeypatch, case
):
    """The payload is written to `<name>.json.tmp` first: that name needs the proof too."""
    repo = _repo(tmp_path, monkeypatch)
    if case == "negation":
        (repo / ".axon").mkdir()
        (repo / ".axon" / ".gitignore").write_text("pending/*\n!*.tmp\n", encoding="utf-8")
    else:
        (repo / ".gitignore").write_text("*.json\n", encoding="utf-8")
        _refuse_replace_onto(monkeypatch, ".gitignore")
    _die_midway(monkeypatch, _Killed())

    with pytest.raises(pending.SpoolNotIgnoredError) as refused:
        await _write()

    assert ".json.tmp" in refused.value.reason
    assert list((repo / ".axon" / "pending").iterdir()) == []
    assert _stageable(repo) == ""


@pytest.mark.parametrize("step", ["write", "rename"])
async def test_failed_payload_write_leaves_no_temp_file(tmp_path, monkeypatch, step):
    repo = _repo(tmp_path, monkeypatch)
    if step == "write":
        _die_midway(monkeypatch, OSError(28, "No space left on device"))
    else:
        real_replace = os.replace

        def replace(src, dst):
            if str(src).endswith(".json.tmp"):
                _read_only_replace()
            real_replace(src, dst)

        monkeypatch.setattr("axon.store.pending.os.replace", replace)

    with pytest.raises(OSError):
        await _write()

    assert list((repo / ".axon" / "pending").iterdir()) == []


@pytest.mark.parametrize("form", ["parameters", "count"])
async def test_injected_git_config_does_not_pass_the_proof(tmp_path, monkeypatch, form):
    """`git -c core.excludesFile=...` reaches hooks through the environment; a plain
    `git add -A` does not have that rule."""
    repo = _repo(tmp_path, monkeypatch)
    excludes = tmp_path / "injected-excludes"
    excludes.write_text(".axon/\n", encoding="utf-8")
    _refuse_replace_onto(monkeypatch, ".gitignore")
    if form == "parameters":
        monkeypatch.setenv("GIT_CONFIG_PARAMETERS", f"'core.excludesFile'='{excludes.as_posix()}'")
    else:
        monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
        monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.excludesFile")
        monkeypatch.setenv("GIT_CONFIG_VALUE_0", excludes.as_posix())

    with pytest.raises(pending.SpoolNotIgnoredError):
        await _write()

    assert list((repo / ".axon").glob("pending/*")) == []
