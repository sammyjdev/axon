from __future__ import annotations

import datetime

import pytest

from axon.activity.models import (
    ActivitySession,
    compute_event_id,
    resolve_activity_project,
)


def test_event_id_is_identical_for_the_same_native_record() -> None:
    id1 = compute_event_id("agy", "src-123")
    id2 = compute_event_id("agy", "src-123")
    id3 = compute_event_id("codex", "src-123")
    id4 = compute_event_id("agy", "src-124")

    assert id1 == id2
    assert id1 != id3
    assert id1 != id4


def test_occurred_at_requires_utc_aware_datetime() -> None:
    from axon.activity.models import ActivityEvent

    naive = datetime.datetime.now()
    aware = datetime.datetime.now(datetime.UTC)

    with pytest.raises(ValueError, match="tz-aware"):
        ActivityEvent(
            event_id="e1",
            harness="agy",
            source_id="s1",
            session_id="ses1",
            occurred_at=naive,
            ingested_at=aware,
            kind="test",
            content={},
            coverage="complete-observable",
            redactions=[],
        )

    event = ActivityEvent(
        event_id="e1",
        harness="agy",
        source_id="s1",
        session_id="ses1",
        occurred_at=aware,
        ingested_at=aware,
        kind="test",
        content={},
        coverage="complete-observable",
        redactions=[],
    )
    assert event.occurred_at == aware


def test_activity_session_project_resolves_through_resolve_repo_key() -> None:
    vault_root = "~/vault"
    known_names = frozenset({"axon"})
    aliases = {}

    path = "~/dev/axon/src"

    key = resolve_activity_project(
        path,
        known_names=known_names,
        aliases=aliases,
        vault_root=vault_root,
    )
    assert key == "axon"

    session = ActivitySession(
        session_id="s1",
        harness="agy",
        source_id="src1",
        project=key,
        workspace=None,
        status="active",
        coverage="complete-observable",
    )
    assert session.project == "axon"


def test_activity_session_project_refuses_rather_than_guesses() -> None:
    vault_root = "~/vault"
    known_names = frozenset({"axon"})
    aliases = {}

    path = "~/dev/unknown-repo/src"

    key = resolve_activity_project(
        path,
        known_names=known_names,
        aliases=aliases,
        vault_root=vault_root,
    )
    assert key is None

    import axon.activity.models
    with open(axon.activity.models.__file__, encoding="utf-8") as f:
        src = f.read()
    assert "import repo_identity" not in src
    assert "\n    repo_identity(" not in src
