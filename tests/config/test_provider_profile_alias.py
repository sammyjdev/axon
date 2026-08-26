from __future__ import annotations

import pytest

from axon.config.runtime import _resolve_provider_profile
from axon.router.profiles import get_profile


def test_resolve_provider_profile_accepts_free_alias(monkeypatch) -> None:
    monkeypatch.setenv("AXON_PROVIDER_PROFILE", "free")
    resolved = _resolve_provider_profile()
    assert resolved == "free"
    assert get_profile(resolved).name == "budget"


def test_resolve_provider_profile_accepts_budget(monkeypatch) -> None:
    monkeypatch.setenv("AXON_PROVIDER_PROFILE", "budget")
    resolved = _resolve_provider_profile()
    assert resolved == "budget"
    assert get_profile(resolved).name == "budget"


def test_resolve_provider_profile_default_is_the_budget_spec(monkeypatch) -> None:
    monkeypatch.delenv("AXON_PROVIDER_PROFILE", raising=False)
    resolved = _resolve_provider_profile()
    assert get_profile(resolved).name == "budget"


def test_resolve_provider_profile_rejects_unknown(monkeypatch) -> None:
    monkeypatch.setenv("AXON_PROVIDER_PROFILE", "bogus")
    with pytest.raises(ValueError):
        _resolve_provider_profile()
