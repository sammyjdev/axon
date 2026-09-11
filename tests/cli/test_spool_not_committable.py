"""Spooled session content must never be committable (axon #184, round 1).

``data_root()`` resolves to ``.axon`` relative to the hook process cwd,
which is the user's project checkout, and in several real repos ``.axon/``
is not gitignored (verified 2026-09-11 in five onboarded repos). The
spooled payload carries conversation content - ``digest_turns`` embeds the
first and last user prompts verbatim and the compact path stores the
harness's full compact summary - so an ordinary ``git add -A && git
commit`` while Postgres is down commits the session.

The fix must ignore exactly the spool directories. It must NOT ignore the
whole ``.axon/`` dir (``context.md`` and ``adr-draft/`` are deliberately
tracked in some repos), must not overwrite a user's existing ignore files,
and must not plant a dotfile inside ``pending-quarantine/``:
``check_quarantine_size`` counts every ``iterdir()`` entry and ``axon
pending recover`` basename-splits every entry (``f.name.rsplit(".", 1)[0]``),
so a dotfile there is miscounted by doctor and breaks recover.

Every ignore-ness assertion reads real git output (``git diff --cached
--name-only`` / ``git status --porcelain --untracked-files=all``), never
the presence of an ignore file. The suite's autouse fixture isolates the
global git config, so a temp repo starts with no excludes anywhere.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

from typer.testing import CliRunner

from axon.cli.pb import app
from axon.doctor.checks.capture import check_quarantine_size
from axon.memory.session_compressor import SessionCompressor
from axon.store.session_store import SessionStore

runner = CliRunner()

_COMPRESSED = "fixed summary: the compressor is faked in this test"


# ---------------------------------------------------------------------------
# git helpers: real git through subprocess with check=True (issue #184 AC8)
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(  # noqa: S603
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True  # noqa: S603, S607
    )
    return proc.stdout


def _staged(repo: Path) -> list[str]:
    """What an ordinary `git add -A` would commit right now."""
    _git(repo, "add", "-A")
    return [line for line in _git(repo, "diff", "--cached", "--name-only").splitlines() if line]


# ---------------------------------------------------------------------------
# hook / store helpers (same style as tests/cli/test_hook_spool.py)
# ---------------------------------------------------------------------------


def _transcript(repo: Path) -> Path:
    path = repo / "t.jsonl"
    path.write_text(
        "".join(
            json.dumps({"type": role, "message": {"role": role, "content": content}}) + "\n"
            for role, content in (
                ("user", "why is my conversation content being committed?"),
                ("assistant", "the spool landed in a tracked .axon/pending"),
                ("user", "git must ignore it"),
            )
        ),
        encoding="utf-8",
    )
    return path


def _fake_compressor(monkeypatch) -> None:
    async def fake_compress(self):
        return _COMPRESSED

    monkeypatch.setattr(SessionCompressor, "compress", fake_compress)


class _StoreDown:
    """SessionStore stand-in whose save fails like an unreachable Postgres."""

    def __init__(self, *_a, **_kw):
        pass

    async def init(self):
        return None

    async def save_session_memory(self, mem):
        raise ConnectionRefusedError(61, "Connect call failed", ("127.0.0.1", 5434))

    async def drain_pending(self):
        return None


class _Repo:
    """Working session repository injected as the store's _session_repo."""

    def __init__(self) -> None:
        self.saved = []

    async def save_session_memory(self, mem) -> int:
        self.saved.append(mem)
        return 1


def _spool_a_session_through_the_hook(repo: Path, monkeypatch) -> None:
    """Drive a store failure through `axon session-hook` so a session is spooled."""
    _fake_compressor(monkeypatch)
    monkeypatch.setattr("axon.store.session_store.SessionStore", _StoreDown)
    payload = json.dumps({"transcript_path": str(_transcript(repo)), "cwd": str(repo)})
    result = runner.invoke(app, ["session-hook"], input=payload)
    assert result.exit_code == 0


def _pending_json(repo: Path) -> list[Path]:
    return sorted((repo / ".axon" / "pending").glob("*.json"))


# ---------------------------------------------------------------------------
# AC8: git add -A stages nothing under .axon/pending/ nor
# .axon/pending-quarantine/
# ---------------------------------------------------------------------------


def test_git_add_all_stages_nothing_under_pending_after_the_hook_spooled(tmp_path, monkeypatch):
    """AC8: a real git repo with no ignore rule for `.axon/`, a store failure
    spools a session through `axon session-hook` - an ordinary `git add -A`
    must stage nothing under `.axon/pending/` while the payload sits there."""
    repo = tmp_path
    monkeypatch.setenv("AXON_DATA_ROOT", str(repo / ".axon"))
    _git(repo, "init")

    # Precondition, proven rather than assumed: nothing ignores the spool
    # yet. check-ignore exits 1 when the path is NOT ignored, which is why
    # this one git call does not use check=True.
    probe = subprocess.run(  # noqa: S603
        ["git", "-C", str(repo), "check-ignore", "-q", ".axon/pending/probe.json"],  # noqa: S603, S607
        capture_output=True,
    )
    assert probe.returncode == 1, "test setup: .axon/pending must start unignored"

    # Controls: ordinary untracked files that the same `git add -A` MUST
    # stage. They prove the add really ran, and that the whole `.axon/` dir
    # is not ignored (context.md is deliberately tracked in real repos).
    (repo / "notes.txt").write_text("ordinary untracked file", encoding="utf-8")
    (repo / ".axon").mkdir(exist_ok=True)
    (repo / ".axon" / "context.md").write_text("tracked by design", encoding="utf-8")

    _spool_a_session_through_the_hook(repo, monkeypatch)

    files = _pending_json(repo)
    assert len(files) == 1, "the session was not spooled to .axon/pending/"
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert payload["kind"] == "session_memory"
    assert payload["summary"] == _COMPRESSED, "the spooled file carries conversation content"

    porcelain = _git(repo, "status", "--porcelain", "--untracked-files=all")
    assert ".axon/pending/" not in porcelain, "git lists the spool as untracked"

    staged = _staged(repo)
    assert "notes.txt" in staged, "control: git add -A staged ordinary files"
    assert ".axon/context.md" in staged, "control: the whole .axon/ dir must stay trackable"
    assert not [p for p in staged if p.startswith(".axon/pending/")], (
        "git add -A staged spooled session content"
    )


def test_git_add_all_stages_nothing_under_quarantine_after_a_drain_quarantined(
    tmp_path, monkeypatch
):
    """AC8: after `SessionStore.drain_pending()` quarantines a malformed
    payload into `.axon/pending-quarantine/`, `git add -A` must stage nothing
    there either. The dir must also stay dotfile-free: doctor counts every
    iterdir() entry and `axon pending recover` basename-splits each one."""
    repo = tmp_path
    monkeypatch.setenv("AXON_DATA_ROOT", str(repo / ".axon"))
    _git(repo, "init")
    _spool_a_session_through_the_hook(repo, monkeypatch)
    assert len(_pending_json(repo)) == 1, "setup: the spool must exist"

    # A malformed payload alongside the valid spooled one.
    (repo / ".axon" / "pending" / "session-bad.json").write_text("{not json", encoding="utf-8")

    store = SessionStore("unused")
    store._session_repo = _Repo()
    result = asyncio.run(store.drain_pending())

    assert result.processed == 1, "the valid spooled session was replayed"
    assert result.quarantined == 1, "the malformed payload was quarantined"
    assert _pending_json(repo) == [], "drain left nothing in pending/"

    quarantine = repo / ".axon" / "pending-quarantine"
    entries = sorted(quarantine.iterdir())
    assert len(entries) == 1, (
        "pending-quarantine/ must hold only the quarantined payload: every entry "
        "is counted by check_quarantine_size and basename-split by pending recover, "
        "so a dotfile planted there breaks both"
    )

    check = check_quarantine_size(data_root=repo / ".axon")
    assert check.detail == "1 quarantined file(s)", (
        "doctor sees more than the quarantined payload in the dir"
    )

    staged = _staged(repo)
    assert not [p for p in staged if p.startswith(".axon/pending/")], (
        "git add -A staged something under .axon/pending/"
    )
    assert not [p for p in staged if p.startswith(".axon/pending-quarantine/")], (
        "git add -A staged the quarantined payload"
    )

    # `axon pending recover` must keep working with whatever the fix put in
    # place: it moves every quarantine entry back by basename-splitting it.
    recovered = runner.invoke(app, ["pending", "recover"])
    assert recovered.exit_code == 0
    assert sorted(quarantine.iterdir()) == []
    assert (repo / ".axon" / "pending" / "session-bad.json").exists()


def test_a_users_preexisting_ignore_files_survive_the_fix(tmp_path, monkeypatch):
    """Constraint: the fix must not overwrite a user's existing ignore file.
    A root `.gitignore` and a hand-edited `.git/info/exclude` keep both their
    content and their effect after the fix has run."""
    repo = tmp_path
    monkeypatch.setenv("AXON_DATA_ROOT", str(repo / ".axon"))
    _git(repo, "init")

    (repo / ".gitignore").write_text("# user marker - hands off\nbuild/\n", encoding="utf-8")
    (repo / "build").mkdir()
    (repo / "build" / "artifact.txt").write_text("ignored by the user", encoding="utf-8")

    exclude = repo / ".git" / "info" / "exclude"
    existing_exclude = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    exclude.parent.mkdir(parents=True, exist_ok=True)
    exclude.write_text(existing_exclude + "vendor/\n# user exclude marker\n", encoding="utf-8")
    (repo / "vendor").mkdir()
    (repo / "vendor" / "lib.txt").write_text("ignored by the user exclude", encoding="utf-8")

    _spool_a_session_through_the_hook(repo, monkeypatch)
    assert len(_pending_json(repo)) == 1, "setup: the spool must exist for the check to bite"

    staged = _staged(repo)
    assert not [p for p in staged if p.startswith(".axon/pending/")], (
        "the fix stopped working in a repo that already had ignore files"
    )
    assert "build/artifact.txt" not in staged, "the user's .gitignore rule was lost"
    assert "vendor/lib.txt" not in staged, "the user's exclude entries were lost"
    assert "# user marker - hands off" in (repo / ".gitignore").read_text(encoding="utf-8"), (
        "the user's .gitignore content was overwritten"
    )
