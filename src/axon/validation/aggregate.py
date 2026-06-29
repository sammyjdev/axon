from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from axon.store.session_store import SessionStore


class ValidationStats(BaseModel):
    model_config = ConfigDict(frozen=True)

    n_total: int
    n_scored: int
    n_passed: int
    pass_rate: float
    threshold: float


async def pass_rate(
    *,
    store: SessionStore,
    repo: str | None = None,
    threshold: float = 3.5,
) -> ValidationStats | None:
    if threshold <= 0:
        raise ValueError(
            f"threshold must be > 0, got {threshold} — 0 or negative would count "
            "every unscored draft as passing"
        )

    stats = await store.validation_stats(repo=repo, threshold=threshold)
    if stats["n_total"] == 0:
        return None
    n_total = stats["n_total"]
    n_scored = stats["n_scored"]
    n_passed = stats["n_passed"]
    rate = (n_passed / n_scored) if n_scored else 0.0
    return ValidationStats(
        n_total=n_total,
        n_scored=n_scored,
        n_passed=n_passed,
        pass_rate=rate,
        threshold=threshold,
    )
