"""Quarantine-side spool ignore coverage (axon #184, test repair 2).

Every earlier quarantine test spooled through ``axon session-hook`` first,
and ``write_pending`` had already written both ignore rules (``pending/``
and ``pending-quarantine/``) into ``.axon/.gitignore`` before quarantine
ever ran. Deleting the ``_ensure_spool_ignored(paths)`` call at the top of
``quarantine_invalid`` therefore changed no test outcome: the mutant
QUARANTINE_CALL_SITE_DROP survived.

This file kills it. The data dir starts with NO ``.axon/.gitignore`` -
the malformed payload is planted directly in ``.axon/pending/``, which is
what a repo looks like when the ignore file was deleted after the spool
was written, or the payload never went through ``write_pending``. The
only code that can make ``.axon/pending-quarantine/`` untracked before
``git add -A`` is then the quarantine-side call itself.

Every ignore-ness assertion reads real git output (``git check-ignore``,
``git status --porcelain``, ``git diff --cached``), never the mere
presence of an ignore file.
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

from axon.store.session_store import SessionStore
from tests.cli.test_spool_not_committable import _git, _staged


def _check_ignore(repo: Path, path: str) -> int:
    """Exit code of `git check-ignore -q`: 0 = ignored, 1 = not ignored."""
    proc = subprocess.run(  # noqa: S603
        ["git", "-C", str(repo), "check-ignore", "-q", path],  # noqa: S603, S607
        capture_output=True,
    )
    return proc.returncode


def test_quarantine_ignores_its_dir_with_no_preexisting_spool_ignore(
    tmp_path, monkeypatch
):
    """A drain that quarantines a payload into a data dir with NO
    ``.axon/.gitignore`` must leave ``.axon/pending-quarantine/`` untracked:
    the quarantine path has to ensure the ignore rule itself, because no
    earlier ``write_pending`` did it for it."""
    repo = tmp_path
    monkeypatch.setenv("AXON_DATA_ROOT", str(repo / ".axon"))
    _git(repo, "init")

    # Precondition, proven rather than assumed: nothing ignores the
    # quarantine dir yet, and no `.axon/.gitignore` exists that a later
    # write could be credited to - a pre-existing ignore file here would
    # mask the exact call site under test.
    assert not (repo / ".axon" / ".gitignore").exists(), (
        "test setup: a pre-existing .axon/.gitignore masks the call site"
    )
    assert _check_ignore(repo, ".axon/pending-quarantine/probe") == 1, (
        "test setup: .axon/pending-quarantine must start unignored"
    )

    # Control: an ordinary untracked file that the same `git add -A` MUST
    # stage, proving the add really ran and the repo is not blanket-ignored.
    (repo / "notes.txt").write_text("ordinary untracked file", encoding="utf-8")

    # A malformed payload in pending/ that never went through write_pending,
    # so nothing has written an ignore rule on its behalf.
    pending = repo / ".axon" / "pending"
    pending.mkdir(parents=True)
    (pending / "session-bad.json").write_text("{not json", encoding="utf-8")

    store = SessionStore("unused")
    result = asyncio.run(store.drain_pending())

    assert result.quarantined == 1, "the malformed payload was not quarantined"
    assert not list(pending.glob("*.json")), "drain left the payload in pending/"
    quarantine = repo / ".axon" / "pending-quarantine"
    entries = sorted(quarantine.iterdir())
    assert len(entries) == 1, "the quarantined payload is not in pending-quarantine/"

    # Real git must now treat the quarantine dir as ignored.
    assert _check_ignore(repo, ".axon/pending-quarantine/probe") == 0, (
        "the quarantine dir is still unignored after the drain"
    )

    porcelain = _git(repo, "status", "--porcelain", "--untracked-files=all")
    assert ".axon/pending-quarantine/" not in porcelain, (
        "git lists the quarantined payload as untracked"
    )

    staged = _staged(repo)
    assert "notes.txt" in staged, "control: git add -A staged ordinary files"
    assert not [p for p in staged if p.startswith(".axon/pending-quarantine/")], (
        "git add -A staged the quarantined payload"
    )
