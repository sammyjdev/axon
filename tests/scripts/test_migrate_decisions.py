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
