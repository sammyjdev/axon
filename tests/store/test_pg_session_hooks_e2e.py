"""#187: the real hooks against real Postgres, through `axon`.

Real testcontainers Postgres; without Docker these error instead of skipping.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

pytest.importorskip("testcontainers.postgres")
import asyncpg  # noqa: E402
from testcontainers.postgres import PostgresContainer  # noqa: E402
from typer.testing import CliRunner  # noqa: E402

from axon.__main__ import app  # noqa: E402

runner = CliRunner()
UNREACHABLE = "postgresql://axon:axon@127.0.0.1:1/axon_tests_have_no_database"


@pytest.fixture(scope="module")
def pg_dsn():
    with PostgresContainer(
        "pgvector/pgvector:pg16", username="axon", password="axon", dbname="axon"
    ) as pg:
        yield pg.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")


async def _reset(dsn: str) -> None:
    con = await asyncpg.connect(dsn)
    try:
        await con.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
    finally:
        await con.close()


async def _fetch(dsn: str) -> list[dict]:
    con = await asyncpg.connect(dsn)
    try:
        return [dict(r) for r in await con.fetch("SELECT * FROM session_memory ORDER BY id")]
    finally:
        await con.close()


def _rows(dsn: str) -> list[dict]:
    return asyncio.run(_fetch(dsn))


@pytest.fixture
def db(pg_dsn, monkeypatch):
    asyncio.run(_reset(pg_dsn))
    monkeypatch.setenv("AXON_PG_URL", pg_dsn)
    return pg_dsn


@pytest.fixture
def repo_dir(tmp_path, monkeypatch) -> Path:
    # an inherited GIT_DIR would point the hook at another repository
    for name in list(os.environ):
        if name.startswith("GIT_") and name not in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM"):
            monkeypatch.delenv(name)
    path = tmp_path / "repo"
    path.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)  # noqa: S607
    return path


def _digest_transcript(tmp_path: Path) -> Path:
    path = tmp_path / "digest.jsonl"
    path.write_text(
        "".join(
            json.dumps({"type": r, "message": {"role": r, "content": c}}) + "\n"
            for r, c in (("user", "fix the upsert"), ("assistant", "done"))
        ),
        encoding="utf-8",
    )
    return path


def _compact_transcript(tmp_path: Path, summary: str) -> Path:
    path = tmp_path / "compact.jsonl"
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


def _hook(command: str, transcript: Path, cwd: Path):
    payload = {"session_id": "abc", "transcript_path": str(transcript), "cwd": str(cwd)}
    result = runner.invoke(app, [command], input=json.dumps(payload))
    assert result.exit_code == 0, result.output
    return result


def _compact_hook(tmp_path: Path, cwd: Path, size: int):
    return _hook("compact-hook", _compact_transcript(tmp_path, "c" * size), cwd)


def _session_hook(tmp_path: Path, cwd: Path, monkeypatch, size: int):
    monkeypatch.setattr("axon.memory.digest.digest_turns", lambda turns: "d" * size)
    return _hook("session-hook", _digest_transcript(tmp_path), cwd)


def _pending() -> list[Path]:
    return sorted((Path(os.environ["AXON_DATA_ROOT"]) / "pending").glob("*.json"))


def _drain():
    from axon.store.session_store import SessionStore

    async def go():
        store = SessionStore("unused")
        try:
            return await store.drain_pending()
        finally:
            await store.close()

    return asyncio.run(go())


def test_session_hook_twice_keeps_one_row(db, repo_dir, tmp_path) -> None:
    transcript = _digest_transcript(tmp_path)
    _hook("session-hook", transcript, repo_dir)
    _hook("session-hook", transcript, repo_dir)

    assert [r["session_id"] for r in _rows(db)] == ["abc"]


def test_thin_digest_after_compact_does_not_overwrite(db, repo_dir, tmp_path, monkeypatch):
    _compact_hook(tmp_path, repo_dir, 2000)
    _session_hook(tmp_path, repo_dir, monkeypatch, 60)

    rows = _rows(db)
    assert len(rows) == 1
    assert rows[0]["raw_turns"] == 0
    assert rows[0]["summary"] == "c" * 2000
    assert rows[0]["digest_summary"] == "d" * 60


@pytest.mark.parametrize("digest_size", [2000, 2400], ids=["equal-bytes", "more-bytes"])
def test_digest_as_rich_as_compact_replaces_it(
    db, repo_dir, tmp_path, monkeypatch, digest_size
) -> None:
    _compact_hook(tmp_path, repo_dir, 2000)
    _session_hook(tmp_path, repo_dir, monkeypatch, digest_size)

    rows = _rows(db)
    assert len(rows) == 1
    assert rows[0]["summary"] == "d" * digest_size
    assert rows[0]["raw_turns"] == 2


def test_late_drain_after_newer_save(db, repo_dir, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AXON_PG_URL", UNREACHABLE)
    _session_hook(tmp_path, repo_dir, monkeypatch, 60)
    spooled = _pending()
    assert len(spooled) == 1
    older = spooled[0].read_bytes()
    payload = json.loads(older)
    assert payload["session_id"] == "abc"
    spooled[0].unlink()  # keep the next save from draining it early

    monkeypatch.setenv("AXON_PG_URL", db)
    _session_hook(tmp_path, repo_dir, monkeypatch, 80)
    before = _rows(db)
    spooled[0].write_bytes(older)

    result = _drain()

    assert result.processed == 1
    assert _pending() == []
    rows = _rows(db)
    assert rows == before
    assert len(rows) == 1
    assert rows[0]["summary"] == "d" * 80
    assert rows[0]["created_at"] > datetime.fromisoformat(payload["created_at"])


@pytest.mark.parametrize("digest_size", [60, 2000], ids=["thin-digest", "equal-bytes"])
@pytest.mark.parametrize("reverse", [False, True], ids=["forward", "reverse"])
def test_reverse_replay_gives_the_same_row(
    db, repo_dir, tmp_path, monkeypatch, digest_size, reverse
) -> None:
    if reverse:
        # the compact summary is taken first, with the store down, and arrives last
        monkeypatch.setenv("AXON_PG_URL", UNREACHABLE)
        _compact_hook(tmp_path, repo_dir, 2000)
        held = _pending()
        assert len(held) == 1
        compact_payload = held[0].read_bytes()
        held[0].unlink()  # keep the digest's save from draining it early
        monkeypatch.setenv("AXON_PG_URL", db)
        _session_hook(tmp_path, repo_dir, monkeypatch, digest_size)
        held[0].write_bytes(compact_payload)
        assert _drain().processed == 1
    else:
        _compact_hook(tmp_path, repo_dir, 2000)
        _session_hook(tmp_path, repo_dir, monkeypatch, digest_size)

    rows = _rows(db)
    assert len(rows) == 1
    row = rows[0]
    shown = ("c" * 2000, 0) if digest_size < 2000 else ("d" * 2000, 2)
    assert (row["session_id"], row["summary"], row["raw_turns"]) == ("abc", *shown)
    assert (row["compact_summary"], row["digest_summary"], row["digest_raw_turns"]) == (
        "c" * 2000,
        "d" * digest_size,
        2,
    )
    assert row["compact_created_at"] < row["digest_created_at"]
    picked = "compact_created_at" if shown[1] == 0 else "digest_created_at"
    assert row["created_at"] == row[picked]
