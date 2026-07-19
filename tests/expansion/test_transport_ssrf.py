from __future__ import annotations

import pytest

from axon.expansion.transport import _OPENER, UrllibSourceTransport
from axon.expansion.url_safety import GuardedRedirectHandler


@pytest.mark.asyncio
async def test_fetch_rejects_file_scheme_without_reading_filesystem() -> None:
    with pytest.raises(ValueError):
        await UrllibSourceTransport().fetch("file:///etc/passwd")


@pytest.mark.asyncio
async def test_fetch_rejects_loopback_address() -> None:
    with pytest.raises(ValueError):
        await UrllibSourceTransport().fetch("http://127.0.0.1/")


@pytest.mark.asyncio
async def test_fetch_rejects_cloud_metadata_link_local_address() -> None:
    with pytest.raises(ValueError):
        await UrllibSourceTransport().fetch("http://169.254.169.254/latest/meta-data/")


def test_transport_opener_has_guarded_redirect_handler() -> None:
    # proves _fetch_sync's real opener enforces redirect checks, not just the
    # initial-url check_url_safety() call (a plain build_opener() would still
    # pass every other test in this suite).
    assert any(isinstance(h, GuardedRedirectHandler) for h in _OPENER.handlers)
