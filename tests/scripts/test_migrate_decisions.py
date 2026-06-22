# tests/scripts/test_migrate_decisions.py
from __future__ import annotations


class _FakeRepo:
    def __init__(self, decisions=None, adrs=None):
        self._decisions = decisions or []
        self._adrs = adrs or []
        self.saved_decisions = []
        self.saved_adrs = []

    async def all_decisions(self):
        return self._decisions

    async def get_adrs(self, project, limit=10):
        return [a for a in self._adrs if a.project == project][:limit]

    async def save_decision(self, d):
        self.saved_decisions.append(d.id)

    async def save_adr_inner(self, a):
        self.saved_adrs.append(a.title)
        return len(self.saved_adrs)


class _IdempotentFakeDstRepo:
    """Simulates a destination that de-duplicates ADRs on (project, title, created_at)."""

    def __init__(self):
        self._decision_ids: list[str] = []
        self._adr_keys: list[tuple] = []  # (project, title, created_at)

    async def save_decision(self, d):
        if d.id not in self._decision_ids:
            self._decision_ids.append(d.id)

    async def save_adr_inner(self, a):
        key = (a.project, a.title, a.created_at)
        if key not in self._adr_keys:
            self._adr_keys.append(key)
        return self._adr_keys.index(key) + 1

    @property
    def decision_count(self):
        return len(self._decision_ids)

    @property
    def adr_count(self):
        return len(self._adr_keys)


async def test_copy_decisions_counts() -> None:
    from datetime import UTC, datetime

    from axon.core.decision import Decision
    from scripts.migrate_decisions import copy_decisions

    d = Decision(id="dec-001", timestamp=datetime(2026, 1, 1, tzinfo=UTC), agent="manual",
                 repo="axon", summary="s")
    src = _FakeRepo(decisions=[d], adrs=[])
    dst = _FakeRepo()
    n_dec, n_adr = await copy_decisions(src, dst, adr_projects=[])
    assert (n_dec, n_adr) == (1, 0)
    assert dst.saved_decisions == ["dec-001"]


async def test_copy_decisions_adr_rerun_is_idempotent() -> None:
    """Running copy_decisions twice must not grow the ADR count in the destination."""
    from datetime import UTC, datetime

    from axon.core.decision import Decision
    from axon.store.session_store import ADR
    from scripts.migrate_decisions import copy_decisions

    d = Decision(id="dec-001", timestamp=datetime(2026, 1, 1, tzinfo=UTC), agent="manual",
                 repo="axon", summary="s")
    a = ADR(project="axon", title="use-postgres", context="c", decision="d", rationale="r",
            created_at=datetime(2026, 1, 1, tzinfo=UTC))
    src = _FakeRepo(decisions=[d], adrs=[a])
    dst = _IdempotentFakeDstRepo()

    n_dec1, n_adr1 = await copy_decisions(src, dst, adr_projects=["axon"])
    assert (n_dec1, n_adr1) == (1, 1)
    assert dst.adr_count == 1

    # Re-run - counts returned by copy_decisions reflect what was iterated, but the
    # destination must not accumulate duplicates.
    n_dec2, n_adr2 = await copy_decisions(src, dst, adr_projects=["axon"])
    assert (n_dec2, n_adr2) == (1, 1)
    assert dst.adr_count == 1, (
        f"ADR count grew from 1 to {dst.adr_count} on re-run - destination is not idempotent"
    )
