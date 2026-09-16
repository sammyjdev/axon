from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, field_validator

from axon.core.repo_identity import resolve_repo_key


def compute_event_id(harness: str, source_id: str) -> str:
    """Return a deterministic event ID."""
    return hashlib.sha256(f"{harness}:{source_id}".encode()).hexdigest()


def resolve_activity_project(
    path: str,
    *,
    known_names: frozenset[str],
    aliases: dict[str, str],
    vault_root: Path,
) -> str | None:
    """Return the resolved repo key for `path`, or None if resolution refuses.

    Never falls back to a basename or to axon.core.repo_identity.repo_identity().
    A refusal (RepoKeyResolution.key is None) returns None - callers must not
    guess.
    """
    res = resolve_repo_key(
        path,
        known_names=known_names,
        aliases=aliases,
        vault_root=vault_root,
    )
    return res.key


def resolve_activity_project_from_cwd(cwd: str | None) -> str | None:
    """Adapter-facing convenience wrapper over `resolve_activity_project`.

    Adapters observe a raw `cwd`/`workspace` string and have no config
    surface of their own for known-repo aliasing, so this always resolves
    with an empty `known_names`/`aliases` set (only the live-directory /
    live-git resolution rules in `resolve_repo_key` can fire) against the
    real configured vault root. `cwd=None` refuses immediately - never calls
    the resolver on a missing value, and never falls back to a basename.
    """
    if not cwd:
        return None
    from axon.config.runtime import load_runtime_config

    runtime = load_runtime_config()
    return resolve_activity_project(
        cwd, known_names=frozenset(), aliases={}, vault_root=runtime.vault_root
    )


class ActivityEvent(BaseModel):
    event_id: str
    schema_version: int = 1
    harness: Literal["claude-code", "codex", "agy"]
    source_id: str
    session_id: str
    turn_id: str | None = None
    call_id: str | None = None
    parent_session_id: str | None = None
    occurred_at: datetime
    ingested_at: datetime
    kind: str
    content: dict[str, object]
    outcome: str | None = None
    coverage: str
    redactions: list[str]

    @field_validator("occurred_at", "ingested_at")
    @classmethod
    def check_utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("datetime must be tz-aware")
        return v


class ActivitySession(BaseModel):
    session_id: str
    harness: Literal["claude-code", "codex", "agy"]
    source_id: str
    project: str | None
    workspace: str | None
    parent_session_id: str | None = None
    status: str
    coverage: str


class SourceCursor(BaseModel):
    session_id: str
    harness: Literal["claude-code", "codex", "agy"]
    source_id: str
    fingerprint: str


class EvidenceLink(BaseModel):
    session_id: str
    harness: Literal["claude-code", "codex", "agy"]
    source_id: str
    fingerprint: str


class ActivityFilters(BaseModel):
    project: str | None = None
    harness: Literal["claude-code", "codex", "agy"] | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None
    outcome: str | None = None


class ActivityPage(BaseModel):
    events: list[ActivityEvent]
    next_cursor: str | None = None
