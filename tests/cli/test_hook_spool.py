"""The Stop/SessionEnd and PostCompact hooks must not lose a session when
the store is unreachable (axon #184).

Observed 2026-09-10: Postgres down for 8h, every `axon session-hook` run
printed the connection error and discarded the computed summary. The fix
reuses the dec-112 pending spool: on store failure the summary is written
to ``$AXON_DATA_ROOT/pending`` as a ``session_memory`` payload, replayed by
the next successful save (or by ``axon pending drain``), and surfaced by
``axon doctor`` without any doctor-side change.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from typer.testing import CliRunner

from axon.cli import pb
from axon.cli.pb import app
from axon.memory.session_compressor import SessionCompressor
from axon.store.session_store import SessionStore

runner = CliRunner()

_COMPRESSED = "fixed summary: the compressor is faked in this test"
_SPOOLED_SUMMARY = "spooled while postgres was down"
_SPOOLED_CREATED_AT = "2026-09-10T08:00:00+00:00"


def _payload(**kw) -> str:
    return json.dumps(kw)


def _transcript(tmp_path, *turns):
    path = tmp_path / "t.jsonl"
    path.write_text(
        "".join(
            json.dumps({"type": r, "message": {"role": r, "content": c}}) + "\n"
            for r, c in turns
        ),
        encoding="utf-8",
    )
    return path


def _compact_transcript(tmp_path, summary):
    path = tmp_path / "c.jsonl"
    path.write_text(
        json.dumps(
            {
                "type": "user",
                "isCompactSummary": True,
                "message": {"role": "user", "content": [{"type": "text", "text": summary}]},
            }
        )
        + "\n",
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


def _spool_files(data_root: Path) -> list[Path]:
    return sorted((data_root / "pending").glob("*.json"))


def _plant_spooled_session(data_root: Path) -> None:
    """A session_memory payload an earlier hook run left while the store was down."""
    pending = data_root / "pending"
    pending.mkdir(parents=True, exist_ok=True)
    (pending / "session-1.json").write_text(
        json.dumps(
            {
                "kind": "session_memory",
                "project": "axon",
                "summary": _SPOOLED_SUMMARY,
                "raw_turns": 9,
                "created_at": _SPOOLED_CREATED_AT,
            }
        ),
        encoding="utf-8",
    )


def test_session_hook_spools_the_summary_when_the_store_save_fails(tmp_path, monkeypatch):
    """AC1: the Stop/SessionEnd path. Exit 0 AND a payload on disk carrying
    exactly what would have been saved."""
    data_root = tmp_path / ".axon"
    monkeypatch.setenv("AXON_DATA_ROOT", str(data_root))
    _fake_compressor(monkeypatch)
    monkeypatch.setattr("axon.store.session_store.SessionStore", _StoreDown)

    path = _transcript(
        tmp_path,
        ("user", "why did every session in the window vanish?"),
        ("assistant", "the hook printed the error and dropped the summary"),
        ("user", "spool it instead"),
    )

    result = runner.invoke(
        app, ["session-hook"], input=_payload(transcript_path=str(path), cwd=str(tmp_path))
    )

    assert result.exit_code == 0
    files = _spool_files(data_root)
    assert len(files) == 1, "the computed summary was not spooled to pending/"
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert payload["kind"] == "session_memory"
    assert payload["project"] == tmp_path.name
    assert payload["summary"] == _COMPRESSED
    assert payload["raw_turns"] == 3
    created_at = datetime.fromisoformat(payload["created_at"])
    assert created_at.utcoffset() is not None, "created_at must carry the UTC offset"


def test_any_store_exception_spools_not_only_connection_errors(tmp_path, monkeypatch):
    """A non-transient failure still spools: losing the computed summary is
    the bug being fixed; drain quarantine decides the rest later."""
    data_root = tmp_path / ".axon"
    monkeypatch.setenv("AXON_DATA_ROOT", str(data_root))
    _fake_compressor(monkeypatch)

    class _Broken(_StoreDown):
        async def save_session_memory(self, mem):
            raise RuntimeError("not a connection error")

    monkeypatch.setattr("axon.store.session_store.SessionStore", _Broken)

    path = _transcript(tmp_path, ("user", "a"), ("assistant", "b"))
    result = runner.invoke(
        app, ["session-hook"], input=_payload(transcript_path=str(path), cwd=str(tmp_path))
    )

    assert result.exit_code == 0
    assert len(_spool_files(data_root)) == 1


def test_compact_hook_spools_with_raw_turns_zero_when_the_store_save_fails(
    tmp_path, monkeypatch
):
    """AC2: the PostCompact path. The harness's compact summary survives the
    outage the same way, with raw_turns == 0."""
    data_root = tmp_path / ".axon"
    monkeypatch.setenv("AXON_DATA_ROOT", str(data_root))
    monkeypatch.setattr("axon.store.session_store.SessionStore", _StoreDown)

    path = _compact_transcript(tmp_path, "the harness compact summary that must survive")

    result = runner.invoke(
        app, ["compact-hook"], input=_payload(transcript_path=str(path), cwd=str(tmp_path))
    )

    assert result.exit_code == 0
    files = _spool_files(data_root)
    assert len(files) == 1, "the compact summary was not spooled to pending/"
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert payload["kind"] == "session_memory"
    assert payload["project"] == tmp_path.name
    assert payload["summary"] == "the harness compact summary that must survive"
    assert payload["raw_turns"] == 0
    datetime.fromisoformat(payload["created_at"])


def test_successful_session_save_also_replays_a_spooled_session(tmp_path, monkeypatch):
    """AC5: dec-112 lists 'next successful capture' as a drain trigger. With a
    spooled payload present and a working store, one session_save call saves
    the new session AND replays the spooled one."""
    data_root = tmp_path / ".axon"
    monkeypatch.setenv("AXON_DATA_ROOT", str(data_root))
    _fake_compressor(monkeypatch)
    _plant_spooled_session(data_root)

    saved = []

    class _Repo:
        async def save_session_memory(self, mem):
            saved.append(mem)
            return 1

    class _Store(SessionStore):
        # Real SessionStore with an injected session repository, so the real
        # save and drain code paths run against a working fake store.
        def __init__(self, *_a, **_kw):
            super().__init__(*_a, **_kw)
            self._session_repo = _Repo()

    monkeypatch.setattr("axon.store.session_store.SessionStore", _Store)

    path = _transcript(tmp_path, ("user", "store is back"), ("assistant", "save and replay"))
    pb.session_save(cwd=str(tmp_path), transcript=str(path))

    assert any(m.summary == _COMPRESSED for m in saved), "the new session was not saved"
    assert any(
        m.summary == _SPOOLED_SUMMARY and m.raw_turns == 9 for m in saved
    ), "the spooled session was not replayed by the same call"
    assert _spool_files(data_root) == [], "a successfully replayed payload must be removed"


def test_successful_compact_save_also_replays_a_spooled_session(tmp_path, monkeypatch):
    """AC5 on the PostCompact path: _save_compact_summary drains too."""
    data_root = tmp_path / ".axon"
    monkeypatch.setenv("AXON_DATA_ROOT", str(data_root))
    _plant_spooled_session(data_root)

    saved = []

    class _Repo:
        async def save_session_memory(self, mem):
            saved.append(mem)
            return 1

    class _Store(SessionStore):
        def __init__(self, *_a, **_kw):
            super().__init__(*_a, **_kw)
            self._session_repo = _Repo()

    monkeypatch.setattr("axon.store.session_store.SessionStore", _Store)

    pb._save_compact_summary(project="axon", summary="fresh compact summary")

    assert any(m.summary == "fresh compact summary" for m in saved)
    assert any(
        m.summary == _SPOOLED_SUMMARY and m.raw_turns == 9 for m in saved
    ), "the spooled session was not replayed by the same call"
    assert _spool_files(data_root) == []


def test_a_drain_failure_never_fails_the_save(tmp_path, monkeypatch):
    """AC5: the drain is best-effort. It raises here, and the save must
    still complete without propagating anything."""
    data_root = tmp_path / ".axon"
    monkeypatch.setenv("AXON_DATA_ROOT", str(data_root))
    _fake_compressor(monkeypatch)

    saved = []

    class _Repo:
        async def save_session_memory(self, mem):
            saved.append(mem)
            return 1

    class _Store(SessionStore):
        def __init__(self, *_a, **_kw):
            super().__init__(*_a, **_kw)
            self._session_repo = _Repo()

        async def drain_pending(self):
            raise RuntimeError("drain exploded")

    monkeypatch.setattr("axon.store.session_store.SessionStore", _Store)

    path = _transcript(tmp_path, ("user", "a"), ("assistant", "b"))
    pb.session_save(cwd=str(tmp_path), transcript=str(path))  # must not raise

    assert any(m.summary == _COMPRESSED for m in saved), "a drain failure lost the save"


def test_doctor_surfaces_the_spooled_session(tmp_path, monkeypatch):
    """AC6: the spool lands where the existing doctor check already looks,
    so it is reported without any doctor-side change."""
    from axon.doctor.checks.capture import check_pending_backlog

    data_root = tmp_path / ".axon"
    monkeypatch.setenv("AXON_DATA_ROOT", str(data_root))
    _fake_compressor(monkeypatch)
    monkeypatch.setattr("axon.store.session_store.SessionStore", _StoreDown)

    path = _transcript(tmp_path, ("user", "a"), ("assistant", "b"))
    result = runner.invoke(
        app, ["session-hook"], input=_payload(transcript_path=str(path), cwd=str(tmp_path))
    )
    assert result.exit_code == 0

    check = check_pending_backlog(data_root=data_root)
    assert "1 files in pending/" in check.detail


def test_a_failing_spool_write_prints_and_returns_without_raising(tmp_path, monkeypatch):
    """AC7: store down AND the spool unwritable. The hook still exits 0, and
    neither save path raises: both failures print and return."""
    data_root = tmp_path / ".axon"
    monkeypatch.setenv("AXON_DATA_ROOT", str(data_root))
    data_root.mkdir(parents=True, exist_ok=True)
    # A regular file where pending/ must go makes write_pending fail.
    (data_root / "pending").write_text("not a directory", encoding="utf-8")
    _fake_compressor(monkeypatch)
    monkeypatch.setattr("axon.store.session_store.SessionStore", _StoreDown)

    path = _transcript(tmp_path, ("user", "a"), ("assistant", "b"))
    result = runner.invoke(
        app, ["session-hook"], input=_payload(transcript_path=str(path), cwd=str(tmp_path))
    )
    assert result.exit_code == 0

    # And the functions themselves must not raise when both the store and
    # the spool fail - printing and returning is the whole contract.
    pb.session_save(cwd=str(tmp_path), transcript=str(path))
    pb._save_compact_summary(project=tmp_path.name, summary="compact summary")
