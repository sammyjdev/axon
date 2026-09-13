# tests/scripts/test_purge_test_artifacts.py
"""Tests for scripts/purge_test_artifacts.py.

Verifies:
- Dry run by default lists matching rows and briefs, changing nothing.
- Refuses --apply without explicit scope flags (exit 2).
- Rejects mutually exclusive scope flags (exit 2).
- With --apply and scope, purges matching fixture rows and test briefs.
- Preserves real briefs carrying notes or real note-less briefs not citing fixtures.
- Preserves decisions whose summaries extend fixture summaries rather than equal them.
- Safety notice is printed on dry run.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from axon.core.decision import Decision
from axon.store.session_store import SessionStore
from scripts.purge_test_artifacts import FIXTURE_SUMMARIES, is_test_brief, main, run


def _create_vault(root: Path) -> Path:
    vault = root / "vault"
    (vault / ".obsidian").mkdir(parents=True)
    (vault / "knowledge" / "handoffs").mkdir(parents=True)
    return vault


async def _seed_decisions(summaries: list[str]) -> None:
    store = SessionStore()
    await store.init()
    try:
        for idx, summary in enumerate(summaries, 1):
            decision = Decision(
                id=f"dec-{idx:03d}",
                timestamp=datetime.now(UTC),
                agent="codex",
                repo="axon",
                summary=summary,
            )
            await store.save_decision(decision)
    finally:
        await store.close()


def _brief_with_fixture_no_notes(summary: str) -> str:
    return (
        "# AXON handoff -> codex\n"
        "repo: axon\n"
        "date: 2026-08-29T17:35:12.442346+00:00\n\n"
        "## Recalled context\n"
        "## AXON recall - axon\n"
        f"- dec-001 (rank 0.40): {summary}\n\n"
        "Continue via the AXON MCP server or by reading .axon/context.md.\n"
    )


def _brief_with_notes(summary: str, notes: str) -> str:
    return (
        "# AXON handoff -> codex\n"
        "repo: axon\n"
        "date: 2026-08-29T17:35:12.442346+00:00\n\n"
        "## From this session\n"
        f"{notes}\n\n"
        "## Recalled context\n"
        f"- dec-001: {summary}\n\n"
        "Continue via the AXON MCP server or by reading .axon/context.md.\n"
    )


def _brief_real_noteless(summary: str) -> str:
    return (
        "# AXON handoff -> codex\n"
        "repo: axon\n"
        "date: 2026-08-29T17:35:12.442346+00:00\n\n"
        "## Recalled context\n"
        f"- dec-099: {summary}\n\n"
        "Continue via the AXON MCP server or by reading .axon/context.md.\n"
    )


def test_is_test_brief_conjunctive_logic():
    # Absent notes and contains fixture summary -> test brief
    assert is_test_brief(_brief_with_fixture_no_notes("a decision")) is True
    assert is_test_brief(_brief_with_fixture_no_notes("drop neo4j backend")) is True

    # Has notes section -> preserved even if fixture summary mentioned
    assert is_test_brief(_brief_with_notes("a decision", "caller notes")) is False

    # No notes, but no fixture summary cited -> preserved
    assert is_test_brief(_brief_real_noteless("adopt postgres backend")) is False


async def test_no_flags_lists_and_changes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    vault = _create_vault(tmp_path)
    handoffs = vault / "knowledge" / "handoffs"
    monkeypatch.setenv("AXON_VAULT", str(vault))

    # Seed Postgres with fixture summaries and a real decision
    await _seed_decisions(list(FIXTURE_SUMMARIES) + ["genuine architectural decision"])

    # Seed vault with test briefs and a real brief with notes
    test_brief_file = handoffs / "2026-08-29-axon-codex-173512.md"
    test_brief_content = _brief_with_fixture_no_notes("a decision")
    test_brief_file.write_text(test_brief_content, encoding="utf-8")

    real_brief_file = handoffs / "2026-08-29-axon-codex-180000.md"
    real_brief_content = _brief_with_notes("a decision", "working on indexer")
    real_brief_file.write_text(real_brief_content, encoding="utf-8")

    test_bytes_before = test_brief_file.read_bytes()
    real_bytes_before = real_brief_file.read_bytes()

    exit_code = await run([])
    assert exit_code == 0

    out, err = capsys.readouterr()
    assert "Notice: matching decisions by exact summary" in out
    assert "would delete decision dec-001 (axon): a decision" in out
    assert "would delete brief 2026-08-29-axon-codex-173512.md" in out
    assert "would purge 5 decision(s) and 1 brief(s)" in out

    # Assert decision rows in Postgres are untouched
    store = SessionStore()
    await store.init()
    try:
        all_decs = await store.all_decisions()
        assert len(all_decs) == 6
    finally:
        await store.close()

    # Assert brief files are byte-identical
    assert test_brief_file.exists()
    assert test_brief_file.read_bytes() == test_bytes_before
    assert real_brief_file.exists()
    assert real_brief_file.read_bytes() == real_bytes_before


async def test_apply_without_scope_flag_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    vault = _create_vault(tmp_path)
    handoffs = vault / "knowledge" / "handoffs"
    monkeypatch.setenv("AXON_VAULT", str(vault))

    await _seed_decisions(["a decision"])
    brief = handoffs / "test-brief.md"
    brief.write_text(_brief_with_fixture_no_notes("a decision"), encoding="utf-8")
    brief_bytes = brief.read_bytes()

    exit_code = await run(["--apply"])
    assert exit_code == 2

    out, err = capsys.readouterr()
    assert "Refusing to purge: pass --decisions, --briefs, or --all with --apply." in err

    # Nothing changed
    store = SessionStore()
    await store.init()
    try:
        all_decs = await store.all_decisions()
        assert len(all_decs) == 1
    finally:
        await store.close()

    assert brief.exists()
    assert brief.read_bytes() == brief_bytes


def test_mutually_exclusive_scope_flags_rejected(capsys: pytest.CaptureFixture[str]):
    exit_code = main(["--apply", "--all", "--decisions"])
    assert exit_code == 2
    out, err = capsys.readouterr()
    assert "--all and --decisions/--briefs are mutually exclusive" in err


async def test_apply_with_scope_deletes_fixtures_and_leaves_real(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    vault = _create_vault(tmp_path)
    handoffs = vault / "knowledge" / "handoffs"
    monkeypatch.setenv("AXON_VAULT", str(vault))

    # Seed 5 fixture summaries and 1 real decision
    await _seed_decisions(list(FIXTURE_SUMMARIES) + ["genuine architectural decision"])

    # Seed 2 test briefs citing fixture summaries (no caller notes)
    test_brief_1 = handoffs / "brief-1.md"
    test_brief_1.write_text(_brief_with_fixture_no_notes("a decision"), encoding="utf-8")
    test_brief_2 = handoffs / "brief-2.md"
    test_brief_2.write_text(_brief_with_fixture_no_notes("add redis cache"), encoding="utf-8")

    # Seed 1 real brief with caller notes
    real_brief = handoffs / "brief-real.md"
    real_brief.write_text(
        _brief_with_notes("a decision", "notes from this session"),
        encoding="utf-8",
    )

    exit_code = await run(["--apply", "--all"])
    assert exit_code == 0

    out, err = capsys.readouterr()
    assert "deleted decision dec-001 (axon): a decision" in out
    assert "deleted brief brief-1.md" in out
    assert "deleted brief brief-2.md" in out
    assert "purged 5 decision(s) and 2 brief(s)" in out

    # Assert Postgres: 0 matching fixture rows remain, 1 real decision survives
    store = SessionStore()
    await store.init()
    try:
        all_decs = await store.all_decisions()
        assert len(all_decs) == 1
        assert all_decs[0].summary == "genuine architectural decision"
    finally:
        await store.close()

    # Assert vault: matching test briefs deleted, remaining brief has caller notes
    assert not test_brief_1.exists()
    assert not test_brief_2.exists()
    assert real_brief.exists()
    assert "## From this session" in real_brief.read_text(encoding="utf-8")


async def test_real_noteless_brief_without_fixture_summary_survives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    vault = _create_vault(tmp_path)
    handoffs = vault / "knowledge" / "handoffs"
    monkeypatch.setenv("AXON_VAULT", str(vault))

    # Real brief without caller notes, but citing a non-fixture summary
    real_noteless = handoffs / "brief-real-noteless.md"
    content = _brief_real_noteless("use postgres unified storage backend")
    real_noteless.write_text(content, encoding="utf-8")
    expected_bytes = real_noteless.read_bytes()

    # Test brief without caller notes citing a fixture summary
    test_brief = handoffs / "brief-test.md"
    test_brief.write_text(
        _brief_with_fixture_no_notes("drop neo4j backend"),
        encoding="utf-8",
    )

    exit_code = await run(["--apply", "--briefs"])
    assert exit_code == 0

    out, err = capsys.readouterr()
    assert "deleted brief brief-test.md" in out
    assert "purged 1 brief(s)" in out

    # Real note-less brief must survive byte-identical
    assert real_noteless.exists()
    assert real_noteless.read_bytes() == expected_bytes
    assert not test_brief.exists()


async def test_scoped_decisions_only_does_not_purge_briefs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    vault = _create_vault(tmp_path)
    handoffs = vault / "knowledge" / "handoffs"
    monkeypatch.setenv("AXON_VAULT", str(vault))

    await _seed_decisions(["a decision"])
    test_brief = handoffs / "brief-test.md"
    test_brief.write_text(_brief_with_fixture_no_notes("a decision"), encoding="utf-8")

    exit_code = await run(["--apply", "--decisions"])
    assert exit_code == 0

    # Decision was purged, brief was NOT purged
    store = SessionStore()
    await store.init()
    try:
        all_decs = await store.all_decisions()
        assert len(all_decs) == 0
    finally:
        await store.close()

    assert test_brief.exists()


async def test_exact_match_preserves_extended_fixture_summaries():
    extended = [
        "a decision about the chunker",
        "revisiting a decision",
        "add redis cache to the query path",
    ]
    await _seed_decisions(list(FIXTURE_SUMMARIES) + extended)

    exit_code = await run(["--apply", "--decisions"])
    assert exit_code == 0

    store = SessionStore()
    await store.init()
    try:
        all_decs = await store.all_decisions()
        remaining_summaries = {d.summary for d in all_decs}
        assert remaining_summaries == set(extended)
        for fixture in FIXTURE_SUMMARIES:
            assert fixture not in remaining_summaries
    finally:
        await store.close()

