"""Recover-side spool ignore coverage (axon #198).

``axon pending recover`` is the one path that moves payload files without
calling ``_ensure_spool_ignored``: ``write_pending`` writes both ignore
rules before it creates a payload and ``quarantine_invalid`` re-asserts
them, but recover moves files out of ``pending-quarantine/`` into
``pending/`` on its own. In a data dir with no ``.axon/.gitignore``
(deleted after the spool was written, or a data dir that predates the
#197 fix) the recovered payloads are stageable by an ordinary
``git add -A`` - the same leak class that blocked the first round of
#184, because payloads carry literal prompts or the full compact summary.

This file pins the fix. The data dir starts with NO ``.axon/.gitignore``
and the quarantined payload is planted directly in
``.axon/pending-quarantine/``, never via ``write_pending`` (which would
write the ignore rules on recover's behalf and mask the call site). The
CLI is driven through ``CliRunner`` - the same entry point a user's shell
hits - never by calling ``pending_recover`` as a plain function.

Every ignore-ness assertion reads real git output (``git check-ignore``,
``git status --porcelain``, ``git diff --cached``), never the mere
presence of an ignore file.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from typer.testing import CliRunner

from axon.cli.pb import app
from tests.cli.test_spool_not_committable import _git, _staged

runner = CliRunner()


def _check_ignore(repo: Path, path: str) -> int:
    """Exit code of `git check-ignore -q`: 0 = ignored, 1 = not ignored."""
    proc = subprocess.run(  # noqa: S603
        ["git", "-C", str(repo), "check-ignore", "-q", path],  # noqa: S603, S607
        capture_output=True,
    )
    return proc.returncode


def _plant_quarantined(repo: Path, name: str) -> None:
    """Plant a payload directly in pending-quarantine/, never via write_pending.

    The basename carries a dot suffix so recover's
    ``f.name.rsplit(".", 1)[0]`` round-trips it back to ``*.json``, and is
    not a dotfile: ``check_quarantine_size`` counts every iterdir() entry
    there and recover basename-splits every one.
    """
    quarantine = repo / ".axon" / "pending-quarantine"
    quarantine.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"kind": "session_memory", "summary": "literal user prompt"})
    (quarantine / name).write_text(payload, encoding="utf-8")


def _assert_starts_unignored(repo: Path) -> None:
    """Proven precondition: no spool ignore file, nothing ignored yet.

    A pre-existing ``.axon/.gitignore`` here would mask the exact call
    site under test, and an already-ignored dir makes every later
    ignore-ness assertion pass vacuously.
    """
    assert not (repo / ".axon" / ".gitignore").exists(), (
        "test setup: a pre-existing .axon/.gitignore masks the call site"
    )
    assert _check_ignore(repo, ".axon/pending/probe") == 1, (
        "test setup: .axon/pending must start unignored"
    )
    assert _check_ignore(repo, ".axon/pending-quarantine/probe") == 1, (
        "test setup: .axon/pending-quarantine must start unignored"
    )


def test_recover_ignores_spool_dirs_with_no_preexisting_spool_ignore(tmp_path, monkeypatch):
    """AC1/AC2: `axon pending recover` in a data dir with NO `.axon/.gitignore`
    must leave both `.axon/pending/` and `.axon/pending-quarantine/` ignored by
    real git: recover itself has to write the rules, because no earlier
    write_pending or quarantine call did it for it."""
    repo = tmp_path
    monkeypatch.setenv("AXON_DATA_ROOT", str(repo / ".axon"))
    _git(repo, "init")
    _assert_starts_unignored(repo)

    # Controls: ordinary untracked files that the same `git add -A` MUST
    # stage. notes.txt proves the add really ran and the repo is not
    # blanket-ignored; context.md proves the fix ignores exactly the spool
    # dirs, not the whole .axon/ dir (context.md is deliberately tracked
    # in real repos).
    (repo / "notes.txt").write_text("ordinary untracked file", encoding="utf-8")
    (repo / ".axon").mkdir(exist_ok=True)
    (repo / ".axon" / "context.md").write_text("tracked by design", encoding="utf-8")

    _plant_quarantined(repo, "sess-a.json.111")
    _plant_quarantined(repo, "sess-b.json.222")

    result = runner.invoke(app, ["pending", "recover"])
    assert result.exit_code == 0

    # Unchanged recover behaviour: the payloads moved and the summary printed.
    pending = repo / ".axon" / "pending"
    assert sorted(p.name for p in pending.iterdir()) == ["sess-a.json", "sess-b.json"], (
        "recover no longer moves quarantined payloads back into pending/"
    )
    assert sorted((repo / ".axon" / "pending-quarantine").iterdir()) == [], (
        "recover left a payload behind in pending-quarantine/"
    )
    assert "Recovered 2 file(s)" in result.stdout

    # Real git must now treat BOTH spool dirs as ignored.
    assert _check_ignore(repo, ".axon/pending/probe") == 0, (
        ".axon/pending is still unignored after recover"
    )
    assert _check_ignore(repo, ".axon/pending-quarantine/probe") == 0, (
        ".axon/pending-quarantine is still unignored after recover"
    )

    porcelain = _git(repo, "status", "--porcelain", "--untracked-files=all")
    assert ".axon/pending/" not in porcelain, "git lists the recovered payloads as untracked"
    assert ".axon/pending-quarantine/" not in porcelain, (
        "git lists pending-quarantine/ as untracked"
    )

    staged = _staged(repo)
    assert "notes.txt" in staged, "control: git add -A staged ordinary files"
    assert ".axon/context.md" in staged, "control: the whole .axon/ dir must stay trackable"
    assert not [p for p in staged if p.startswith(".axon/pending/")], (
        "git add -A staged the recovered payloads"
    )
    assert not [p for p in staged if p.startswith(".axon/pending-quarantine/")], (
        "git add -A staged something under pending-quarantine/"
    )


def test_recover_id_filter_leaves_the_unmatched_quarantined_payload_ignored(tmp_path, monkeypatch):
    """AC4 (--id unchanged) with a real file left behind: recovering by --id
    moves only the match, and the payload that STAYS in
    ``pending-quarantine/`` must not be stageable either - this is the
    assertion that bites on the quarantine side, because a real payload
    file is still there for ``git add -A`` to see."""
    repo = tmp_path
    monkeypatch.setenv("AXON_DATA_ROOT", str(repo / ".axon"))
    _git(repo, "init")
    _assert_starts_unignored(repo)

    # Control: an ordinary untracked file that the same `git add -A` MUST
    # stage, proving the add really ran.
    (repo / "notes.txt").write_text("ordinary untracked file", encoding="utf-8")

    _plant_quarantined(repo, "alpha.json.111")
    _plant_quarantined(repo, "beta.json.222")

    result = runner.invoke(app, ["pending", "recover", "--id", "alpha"])
    assert result.exit_code == 0

    # --id filtering still works: only the match moved.
    assert (repo / ".axon" / "pending" / "alpha.json").exists(), (
        "the --id match was not recovered into pending/"
    )
    assert not (repo / ".axon" / "pending" / "beta.json").exists(), (
        "recover moved a payload the --id filter should have skipped"
    )
    assert (repo / ".axon" / "pending-quarantine" / "beta.json.222").exists(), (
        "the unmatched payload did not stay in pending-quarantine/"
    )
    assert "recovered: alpha.json.111" in result.stdout
    assert "Recovered 1 file(s)" in result.stdout

    staged = _staged(repo)
    assert "notes.txt" in staged, "control: git add -A staged ordinary files"
    assert not [p for p in staged if p.startswith(".axon/pending/")], (
        "git add -A staged the recovered payload"
    )
    assert ".axon/pending-quarantine/beta.json.222" not in staged, (
        "git add -A staged the quarantined payload recover left behind"
    )


def test_recover_without_a_quarantine_dir_is_a_pure_noop(tmp_path, monkeypatch):
    """AC4 + placement: with no quarantine dir recover prints "Sem quarantine."
    and must touch nothing - in particular it must NOT create ``.axon/`` and
    write a ``.gitignore``, which is what happens if the ignore call sits
    above the early return instead of next to the payload move."""
    repo = tmp_path
    monkeypatch.setenv("AXON_DATA_ROOT", str(repo / ".axon"))
    _git(repo, "init")

    result = runner.invoke(app, ["pending", "recover"])
    assert result.exit_code == 0
    assert "Sem quarantine." in result.stdout
    assert not (repo / ".axon").exists(), (
        "recover created .axon/ (and its .gitignore) on a read-only no-op path: "
        "the ignore call belongs next to the payload move, after the early return"
    )
