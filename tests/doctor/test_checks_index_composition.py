from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from axon.doctor import CheckStatus
from axon.doctor.checks import index_composition as mod


class _Conn:
    def __init__(self, row: dict[str, object]) -> None:
        self._row = row

    async def fetchrow(self, _sql: str) -> dict[str, object]:
        return self._row

    async def close(self) -> None:
        return None


def test_healthy_composition_returns_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        mod.asyncpg,
        "connect",
        AsyncMock(
            return_value=_Conn(
                {
                    "total_chunks": 5000,
                    "vault_chunks": 250,
                    "career_chunks": 120,
                    "plan_artifacts": 40,
                }
            )
        ),
    )

    result = mod.check_index_composition(pg_url="postgresql://axon", table="embeddings")

    assert result.status is CheckStatus.OK
    assert "total=5000" in result.detail
    assert "vault_share=5.0%" in result.detail


def test_inverted_composition_warns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        mod.asyncpg,
        "connect",
        AsyncMock(
            return_value=_Conn(
                {
                    "total_chunks": 10000,
                    "vault_chunks": 180,
                    "career_chunks": 0,
                    "plan_artifacts": 1201,
                }
            )
        ),
    )

    result = mod.check_index_composition(pg_url="postgresql://axon", table="embeddings")

    assert result.status is CheckStatus.WARN
    assert "vault_share=1.8%" in result.detail
    assert "career=0" in result.detail
    assert "plans=1201" in result.detail


def test_unreachable_db_returns_skipped_warn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        mod.asyncpg,
        "connect",
        AsyncMock(side_effect=OSError("connection refused")),
    )

    result = mod.check_index_composition(pg_url="postgresql://axon", table="embeddings")

    assert result.status is CheckStatus.WARN
    assert result.detail == "skipped: db unreachable"


def test_hung_connection_warns_promptly(monkeypatch):
    import asyncio

    async def _hang(*_args, **_kwargs):
        await asyncio.sleep(60)

    monkeypatch.setattr("asyncpg.connect", _hang)
    monkeypatch.setattr(mod, "_CONNECT_TIMEOUT_S", 0.05)

    result = mod.check_index_composition(pg_url="postgresql://x:x@10.255.255.1:5/x")

    assert result.status is CheckStatus.WARN
    assert "db unreachable" in result.detail
