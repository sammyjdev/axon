from __future__ import annotations

import pytest

from axon.mcp import server


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1", True),
        ("true", True),
        ("YES", True),
        ("on", True),
        ("0", False),
        ("false", False),
        ("", False),
        ("nope", False),
    ],
)
def test_reversible_enabled_env_parsing(monkeypatch, value, expected) -> None:
    monkeypatch.setenv("AXON_RTK_REVERSIBLE", value)
    assert server._reversible_enabled() is expected


def test_reversible_disabled_when_unset(monkeypatch) -> None:
    monkeypatch.delenv("AXON_RTK_REVERSIBLE", raising=False)
    assert server._reversible_enabled() is False
