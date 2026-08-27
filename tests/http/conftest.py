from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_ambient_http_auth_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Scrub ambient HTTP auth env vars so shell exports do not fail tests."""
    monkeypatch.delenv("AXON_HTTP_TOKEN", raising=False)
    monkeypatch.delenv("AXON_HTTP_ALLOW_UNAUTHENTICATED", raising=False)
