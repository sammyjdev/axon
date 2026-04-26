from __future__ import annotations

from prometheus.context.cache_key import build_composite_cache_key


def test_composite_cache_key_changes_with_policy_version() -> None:
    k1 = build_composite_cache_key(
        content="same",
        ctx="knowledge",
        policy_version="v1",
        model="claude-haiku",
        availability="anthropic=1;openrouter=1;ollama=1",
    )
    k2 = build_composite_cache_key(
        content="same",
        ctx="knowledge",
        policy_version="v2",
        model="claude-haiku",
        availability="anthropic=1;openrouter=1;ollama=1",
    )

    assert k1 != k2
