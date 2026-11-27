"""The activity spool refuses like the session spool (#202). No Postgres."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from axon.activity.models import ActivityEvent, SourceCursor
from axon.activity.service import ActivityService
from axon.store.pending import SpoolNotIgnoredError
from tests.cli.test_spool_not_committable import _git


class _Repo:
    async def upsert_session(self, session):
        raise AssertionError("nothing was spooled, so nothing may be drained")

    async def replay_spooled_event(self, event, cursor, *, byte_offset):
        raise AssertionError("nothing was spooled, so nothing may be drained")


async def test_ingest_refuses_unignored_spool(tmp_path, monkeypatch):
    """Criterion 10."""
    monkeypatch.setenv("AXON_DATA_ROOT", str(tmp_path / ".axon"))
    _git(tmp_path, "init")
    (tmp_path / ".axon" / ".gitignore").mkdir(parents=True)
    event = ActivityEvent(
        event_id="evt-1",
        harness="claude-code",
        source_id="src-1",
        session_id="ses-1",
        parent_session_id=None,
        occurred_at=datetime.now(UTC),
        ingested_at=datetime.now(UTC),
        kind="test",
        content={"msg": "literal user prompt"},
        coverage="full",
        redactions=[],
    )
    cursor = SourceCursor(
        session_id="ses-1", harness="claude-code", source_id="src-1", fingerprint="F1"
    )

    with pytest.raises(SpoolNotIgnoredError):
        await ActivityService(_Repo()).ingest_events([event], cursor=cursor)

    assert list((tmp_path / ".axon").glob("activity-pending/*.json")) == []
