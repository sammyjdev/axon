"""Regression: confirm tests' AXON_ENGINE isolation actually redirects writes."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from axon.mcp import server


@pytest.mark.asyncio
async def test_mcp_trace_store_writes_under_axon_engine_tmp(
    tmp_path: Path,
) -> None:
    engine = Path(os.environ["AXON_ENGINE"])
    # the autouse conftest fixture pointed AXON_ENGINE at a tmp dir
    assert engine.exists()
    assert engine != Path.home() / ".axon"
    assert engine != Path.home() / "dev" / "axon"

    # the mcp.server module-level _TRACE_STORE must also be redirected
    assert str(server._TRACE_STORE.records_file).startswith(str(engine))
