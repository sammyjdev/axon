from __future__ import annotations

import hashlib


def build_composite_cache_key(
    *,
    content: str,
    ctx: str | None,
    policy_version: str,
    model: str,
    availability: str,
) -> str:
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return "|".join([
        digest,
        ctx or "auto",
        policy_version,
        model,
        availability,
    ])
