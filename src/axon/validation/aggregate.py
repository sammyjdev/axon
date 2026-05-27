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

    import aiosqlite

    async with store._lock:
        db = await store._connection()
        db.row_factory = aiosqlite.Row
        where = ""
        params: list[object] = []
        if repo is not None:
            where = " WHERE json_extract(frontmatter, '$.repo') = ?"
            params.append(repo)
        rows = await db.execute_fetchall(
            "SELECT"
            " COUNT(*) AS n_total,"
            " SUM(CASE WHEN json_extract(frontmatter, '$.judged') = 1"
            "          THEN 1 ELSE 0 END) AS n_scored,"
            " SUM(CASE WHEN json_extract(frontmatter, '$.judged') = 1"
            "           AND json_extract(frontmatter, '$.validation_score') >= ?"
            "          THEN 1 ELSE 0 END) AS n_passed"
            f" FROM decisions{where}",
            (threshold, *params),
        )

    row = rows[0] if rows else None
    if row is None or row["n_total"] == 0:
        return None
    n_total = int(row["n_total"])
    n_scored = int(row["n_scored"] or 0)
    n_passed = int(row["n_passed"] or 0)
    rate = (n_passed / n_scored) if n_scored else 0.0
    return ValidationStats(
        n_total=n_total,
        n_scored=n_scored,
        n_passed=n_passed,
        pass_rate=rate,
        threshold=threshold,
    )
