"""Edge coverage for the spool ignore rules (axon #184, fix round 1).

Two mutations survived the round-1 battery because every gate fixture
happened to seed ``.gitignore`` with a trailing newline and a writable
ignore path:

- BOUNDARY_NEWLINE_PREFIX: with ``prefix = ""``, appending the spool
  rules to a newline-less ``.gitignore`` glues them onto the user's last
  rule (``local-only.txtpending/``), which both corrupts that rule and
  leaves the spooled session committable.
- EXCEPTION_SWALLOW_IGNORE_WRITE: with ``except OSError: raise``, any
  OSError while writing the ignore file (``.gitignore`` existing as a
  directory makes ``os.replace`` raise) propagates out of
  ``write_pending`` before the payload is written, and the spooled
  session is lost - the exact data loss the spool exists to prevent.

The session-hook CLI swallows every exception by design ("a hook must
never interrupt the agent"), so the OSError mutation cannot be observed
through the exit code; it is observed through the payload's absence.
"""

from __future__ import annotations

import json

from tests.cli.test_spool_not_committable import (
    _git,
    _pending_json,
    _spool_a_session_through_the_hook,
    _staged,
)


def test_spooling_appends_ignore_rule_with_newline_when_gitignore_lacks_trailing_newline(
    tmp_path, monkeypatch
):
    """A ``.gitignore`` whose last line has no trailing newline must not
    have the spool rules glued onto it: the user's rule survives intact on
    its own line and the spool stays uncommittable."""
    repo = tmp_path
    monkeypatch.setenv("AXON_DATA_ROOT", str(repo / ".axon"))
    _git(repo, "init")

    axon_dir = repo / ".axon"
    axon_dir.mkdir()
    ignore = axon_dir / ".gitignore"
    # No trailing newline: the whole point of this edge.
    ignore.write_text("# user rule\nlocal-only.txt", encoding="utf-8")
    (axon_dir / "local-only.txt").write_text("private", encoding="utf-8")

    _spool_a_session_through_the_hook(repo, monkeypatch)
    assert len(_pending_json(repo)) == 1, "setup: the spool must exist for the check to bite"

    content = ignore.read_text(encoding="utf-8")
    lines = content.splitlines()
    assert "local-only.txtpending/" not in content, (
        "the spool rule was glued onto the user's last line"
    )
    assert "local-only.txt" in lines, "the user's rule lost its own line"
    assert "pending/" in lines, "the spool rule was not appended on a new line"

    porcelain = _git(repo, "status", "--porcelain", "--untracked-files=all")
    assert ".axon/local-only.txt" not in porcelain, (
        "the corrupted rule stopped ignoring the user's file"
    )
    assert ".axon/pending/" not in porcelain, "git lists the spool as untracked"

    staged = _staged(repo)
    assert ".axon/.gitignore" in staged, "control: git add -A staged ordinary files"
    assert ".axon/local-only.txt" not in staged, "git add -A staged the user's private file"
    assert not [p for p in staged if p.startswith(".axon/pending/")], (
        "git add -A staged spooled session content"
    )


def test_ignore_file_write_oserror_is_swallowed_during_session_hook_spool(
    tmp_path, monkeypatch
):
    """An OSError while writing the ignore file must not take the payload
    down with it: ``.gitignore`` existing as a directory makes the ignore
    write fail, and the session must still be spooled to ``pending/``."""
    repo = tmp_path
    monkeypatch.setenv("AXON_DATA_ROOT", str(repo / ".axon"))
    _git(repo, "init")

    # A directory where the ignore file should be: os.replace onto it
    # raises OSError while .axon/pending/ stays perfectly writable.
    (repo / ".axon" / ".gitignore").mkdir(parents=True)

    _spool_a_session_through_the_hook(repo, monkeypatch)

    files = _pending_json(repo)
    assert len(files) == 1, (
        "the ignore-write failure took the spool down: the session was lost"
    )
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert payload["kind"] == "session_memory"
