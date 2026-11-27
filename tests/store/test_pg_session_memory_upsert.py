"""#187: one session_memory row per session_id, picked the same way in any arrival order.

Real testcontainers Postgres; without Docker these error instead of skipping.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

pytest.importorskip("testcontainers.postgres")
import asyncpg  # noqa: E402
from testcontainers.postgres import PostgresContainer  # noqa: E402

from axon.store.pg_session_repository import PostgresSessionRepository  # noqa: E402
from axon.store.session_store import SessionMemory  # noqa: E402

T1 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
T2 = T1 + timedelta(minutes=5)
T3 = T1 + timedelta(minutes=10)


@pytest.fixture(scope="module")
def pg_dsn():
    with PostgresContainer(
        "pgvector/pgvector:pg16", username="axon", password="axon", dbname="axon"
    ) as pg:
        yield pg.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")


@pytest.fixture
async def repo(pg_dsn):
    con = await asyncpg.connect(pg_dsn)
    try:
        await con.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
    finally:
        await con.close()
    repository = PostgresSessionRepository(dsn=pg_dsn)
    await repository.ensure_schema()
    yield repository
    await repository.close()


def _mem(
    summary: str,
    *,
    turns: int = 4,
    at: datetime = T1,
    sid: str | None = "s-1",
    project: str = "p",
) -> SessionMemory:
    return SessionMemory(
        project=project, summary=summary, raw_turns=turns, created_at=at, session_id=sid
    )


def _compact(summary: str, **kwargs) -> SessionMemory:
    return _mem(summary, turns=0, **kwargs)


async def _fetch(pg_dsn: str, sql: str, *args) -> list[dict]:
    con = await asyncpg.connect(pg_dsn)
    try:
        return [dict(r) for r in await con.fetch(sql, *args)]
    finally:
        await con.close()


async def _rows(pg_dsn: str, sid: str = "s-1") -> list[dict]:
    return await _fetch(pg_dsn, "SELECT * FROM session_memory WHERE session_id = $1", sid)


def _content(row: dict) -> dict:
    return {k: v for k, v in row.items() if k not in ("id", "session_id")}


async def test_same_session_id_upserts_one_row(repo, pg_dsn) -> None:
    first = await repo.save_session_memory(_mem("first", at=T1))
    second = await repo.save_session_memory(_mem("second", at=T2))

    rows = await _rows(pg_dsn)
    assert len(rows) == 1
    assert rows[0]["summary"] == "second"
    assert rows[0]["created_at"] == T2
    assert first == second == rows[0]["id"]


async def test_older_snapshot_does_not_overwrite_and_returns_the_id(repo, pg_dsn) -> None:
    first = await repo.save_session_memory(_mem("newer", at=T2))
    before = await _rows(pg_dsn)

    late = await repo.save_session_memory(_mem("older and much longer than newer", at=T1))

    assert late == first
    assert await _rows(pg_dsn) == before


async def test_rows_without_session_id_keep_natural_key(repo, pg_dsn) -> None:
    a = await repo.save_session_memory(_mem("a", sid=None))
    b = await repo.save_session_memory(_mem("b", sid=None))
    again = await repo.save_session_memory(_mem("a", sid=None))

    assert a != b
    assert again == a
    rows = await _fetch(pg_dsn, "SELECT * FROM session_memory ORDER BY id")
    assert [r["summary"] for r in rows] == ["a", "b"]
    for row in rows:
        assert row["session_id"] is None
        assert row["compact_summary"] is None
        assert row["compact_created_at"] is None
        assert row["digest_summary"] is None
        assert row["digest_raw_turns"] is None
        assert row["digest_created_at"] is None


async def test_other_project_does_not_take_over_a_session(repo, pg_dsn) -> None:
    first = await repo.save_session_memory(_mem("mine", at=T1, project="p"))
    before = await _rows(pg_dsn)

    other = await repo.save_session_memory(
        _mem("theirs, newer and longer", at=T2, project="other")
    )
    other_compact = await repo.save_session_memory(
        _compact("theirs compact", at=T3, project="other")
    )

    assert other == first
    assert other_compact == first
    assert await _rows(pg_dsn) == before
    assert len(await _fetch(pg_dsn, "SELECT id FROM session_memory")) == 1


async def test_identical_content_in_two_sessions_keeps_two_rows(repo, pg_dsn) -> None:
    one = await repo.save_session_memory(_mem("same", sid="s-1"))
    two = await repo.save_session_memory(_mem("same", sid="s-2"))
    unkeyed = await repo.save_session_memory(_mem("same", sid=None))

    assert len({one, two, unkeyed}) == 3
    assert len(await _fetch(pg_dsn, "SELECT id FROM session_memory")) == 3


@pytest.mark.parametrize(
    ("low", "high"),
    [
        # more bytes wins, even when it sorts lower
        (("zz", 4), ("aaaaaa", 4)),
        # equal bytes: higher in byte order ("a" is above "B" in bytes, below it in a locale)
        (("BBBB", 4), ("aaaa", 4)),
        # equal text: more raw_turns
        (("same", 2), ("same", 5)),
    ],
    ids=["bytes", "byte-order", "raw-turns"],
)
@pytest.mark.parametrize("reverse", [False, True], ids=["forward", "reverse"])
async def test_equal_time_is_arrival_independent(repo, pg_dsn, low, high, reverse) -> None:
    snapshots = [_mem(low[0], turns=low[1]), _mem(high[0], turns=high[1])]
    if reverse:
        snapshots.reverse()
    ids = [await repo.save_session_memory(s) for s in snapshots]

    rows = await _rows(pg_dsn)
    assert len(rows) == 1
    assert (rows[0]["summary"], rows[0]["raw_turns"]) == high
    assert ids[0] == ids[1] == rows[0]["id"]

    # an identical snapshot changes neither the row nor its id
    assert await repo.save_session_memory(_mem(high[0], turns=high[1])) == ids[0]
    assert await _rows(pg_dsn) == rows


@pytest.mark.parametrize("second_turns", [4, 0], ids=["newer-digest", "newer-compact"])
async def test_newer_digest_or_compact_replaces(repo, pg_dsn, second_turns) -> None:
    # no compact summary: the newer digest wins even when thinner; and a compact
    # summary newer than every digest wins even when thinner
    await repo.save_session_memory(_mem("a long older digest " * 5, at=T1))
    await repo.save_session_memory(_mem("thin", turns=second_turns, at=T2))

    rows = await _rows(pg_dsn)
    assert len(rows) == 1
    assert (rows[0]["summary"], rows[0]["raw_turns"], rows[0]["created_at"]) == (
        "thin",
        second_turns,
        T2,
    )


@pytest.mark.parametrize("compact_first", [True, False], ids=["compact-first", "digest-first"])
async def test_equal_text_cross_kind_tie_is_deterministic(repo, pg_dsn, compact_first) -> None:
    text = "x" * 100
    pair = [_compact(text, at=T1), _mem(text, turns=2, at=T1)]
    if not compact_first:
        pair.reverse()
    for snapshot in pair:
        await repo.save_session_memory(snapshot)

    row = (await _rows(pg_dsn))[0]
    assert _content(row) == {
        "project": "p",
        "summary": text,
        "raw_turns": 0,
        "created_at": T1,
        "compact_summary": text,
        "compact_created_at": T1,
        "digest_summary": text,
        "digest_raw_turns": 2,
        "digest_created_at": T1,
    }

    await repo.save_session_memory(_mem("d" * 40, turns=3, at=T2))

    row = (await _rows(pg_dsn))[0]
    assert (row["summary"], row["raw_turns"], row["created_at"]) == (text, 0, T1)


async def test_concurrent_upserts_keep_one_row(repo, pg_dsn) -> None:
    pairs = {
        "digests": (_mem("older digest", at=T1), _mem("newer digest", at=T2)),
        "mixed": (_compact("c" * 500, at=T1), _mem("thin", at=T2)),
    }
    for name, (a, b) in pairs.items():
        sequential = []
        for order, snapshots in enumerate(([a, b], [b, a])):
            sid = f"{name}-seq-{order}"
            for snapshot in snapshots:
                await repo.save_session_memory(snapshot.model_copy(update={"session_id": sid}))
            sequential.append(_content((await _rows(pg_dsn, sid))[0]))
        assert sequential[0] == sequential[1]

        for run in range(20):
            sid = f"{name}-{run}"
            ids = await asyncio.gather(
                repo.save_session_memory(a.model_copy(update={"session_id": sid})),
                repo.save_session_memory(b.model_copy(update={"session_id": sid})),
            )
            rows = await _rows(pg_dsn, sid)
            assert len(rows) == 1
            assert ids[0] == ids[1] == rows[0]["id"]
            assert _content(rows[0]) == sequential[0]


async def test_readback_carries_session_id_and_stable_order(repo) -> None:
    first = await repo.save_session_memory(_mem("one", sid="s-1", at=T1))
    second = await repo.save_session_memory(_mem("two", sid="s-2", at=T1))
    unkeyed = await repo.save_session_memory(_mem("three", sid=None, at=T1))
    # rewrite the first row so its physical position is no longer its id order
    await repo.save_session_memory(_mem("one again", sid="s-1", at=T1))

    recalled = await repo.get_session_memories("p", limit=3)
    assert [(m.id, m.session_id) for m in recalled] == [
        (unkeyed, None),
        (second, "s-2"),
        (first, "s-1"),
    ]
    assert [(m.id, m.session_id) for m in await repo.all_memories()] == [
        (first, "s-1"),
        (second, "s-2"),
        (unkeyed, None),
    ]
