"""
Testes unitários dos stores — T-034.
graph_store e session_store usam Testcontainers.
collections.py é testado sem infra (lógica pura).
vector_store requer Qdrant real — testa apenas a lógica de agrupamento/batch.
"""

from collections.abc import AsyncGenerator

import pytest

from axon.store.collections import get_search_collections
from axon.store.failure_store import FailureRecord, FailureStore
from axon.store.outcome_store import OutcomeRecord, OutcomeStore
from axon.store.session_store import ADR, CodeChange, SessionMemory, SessionStore

# ── collections.py ─────────────────────────────────────────────────────────────


class TestGetSearchCollections:
    def test_no_ctx_excludes_work(self) -> None:
        result = get_search_collections(None)
        assert "work" not in result
        assert set(result) == {"personal", "career", "knowledge", "saas"}

    def test_explicit_work_ctx_returns_only_work(self) -> None:
        result = get_search_collections("work")
        assert result == ["work"]

    def test_personal_ctx_excludes_work(self) -> None:
        result = get_search_collections("personal")
        assert result == ["personal"]

    def test_explicit_non_work_ctx_returns_only_that_context(self) -> None:
        assert get_search_collections("knowledge") == ["knowledge"]
        assert get_search_collections("career") == ["career"]
        assert get_search_collections("saas") == ["saas"]

    def test_empty_string_ctx_excludes_work(self) -> None:
        result = get_search_collections("")
        assert "work" not in result


# ── session_store.py ────────────────────────────────────────────────────────────


@pytest.fixture
async def session_store(tmp_path, monkeypatch) -> AsyncGenerator[SessionStore, None]:
    # Isolated per-test SQLite store; pin graph + decisions backends so these
    # tests do not route to the shared postgres after the wave-2/3 cutover flips.
    monkeypatch.setenv("AXON_GRAPH_BACKEND", "sqlite")
    monkeypatch.setenv("AXON_DECISIONS_BACKEND", "sqlite")
    store = SessionStore(db_path=tmp_path / "test.db")
    await store.init()
    yield store
    await store.close()


@pytest.mark.asyncio
class TestSessionStore:
    async def test_save_and_get_adr(self, session_store) -> None:
        adr = ADR(
            project="aerus-rpg",
            title="Usar event sourcing para combate",
            context="Precisamos replay de estados",
            decision="Event sourcing com Redis Streams",
            rationale="Facilita undo/redo e replay",
        )
        adr_id = await session_store.save_adr(adr)
        assert adr_id > 0

        adrs = await session_store.get_adrs("aerus-rpg")
        assert len(adrs) == 1
        assert adrs[0].title == "Usar event sourcing para combate"
        assert adrs[0].project == "aerus-rpg"

    async def test_get_adrs_empty_project(self, session_store) -> None:
        adrs = await session_store.get_adrs("nonexistent")
        assert adrs == []

    async def test_save_and_get_session_memory(self, session_store) -> None:
        mem = SessionMemory(
            project="aerus-rpg",
            summary="Implementamos o sistema de combate com turnos.",
            raw_turns=15,
        )
        mem_id = await session_store.save_session_memory(mem)
        assert mem_id > 0

        mems = await session_store.get_session_memories("aerus-rpg")
        assert len(mems) == 1
        assert mems[0].raw_turns == 15
        assert "combate" in mems[0].summary

    async def test_session_memory_respects_limit(self, session_store) -> None:
        for i in range(5):
            await session_store.save_session_memory(
                SessionMemory(project="p", summary=f"session {i}", raw_turns=i)
            )
        mems = await session_store.get_session_memories("p", limit=3)
        assert len(mems) == 3

    async def test_save_and_get_code_change(self, session_store) -> None:
        change = CodeChange(
            commit_hash="abc123",
            file_path="src/combat/Engine.java",
            diff_summary="Added turn-based combat loop",
            why="feat: implement combat engine",
        )
        await session_store.save_code_change(change)

        changes = await session_store.get_recent_changes("src/combat/Engine.java")
        assert len(changes) == 1
        assert changes[0].commit_hash == "abc123"

    async def test_code_change_upsert_on_duplicate_key(self, session_store) -> None:
        change = CodeChange(
            commit_hash="abc123",
            file_path="src/Engine.java",
            diff_summary="v1",
        )
        await session_store.save_code_change(change)

        change2 = CodeChange(
            commit_hash="abc123",
            file_path="src/Engine.java",
            diff_summary="v2 updated",
        )
        await session_store.save_code_change(change2)

        changes = await session_store.get_recent_changes("src/Engine.java")
        assert len(changes) == 1
        assert changes[0].diff_summary == "v2 updated"

    async def test_get_recent_changes_empty(self, session_store) -> None:
        changes = await session_store.get_recent_changes("nonexistent.java")
        assert changes == []


@pytest.fixture
async def failure_store(tmp_path) -> AsyncGenerator[FailureStore, None]:
    store = FailureStore(db_path=tmp_path / "failure.db")
    await store.init()
    yield store
    await store.close()


@pytest.mark.asyncio
class TestFailureStore:
    async def test_save_and_get_recent_failures(self, failure_store) -> None:
        record = FailureRecord(
            project="axon",
            operation="til-promotion",
            error_message="promotion failed after duplicate note match",
            probable_cause="duplicate detection threshold too low",
            tags=["til", "promotion"],
        )
        record_id = await failure_store.save_failure(record)

        failures = await failure_store.get_recent_failures("axon")
        assert record_id > 0
        assert len(failures) == 1
        assert failures[0].probable_cause == "duplicate detection threshold too low"
        assert failures[0].tags == ["til", "promotion"]

    async def test_get_recent_failures_respects_project_and_limit(self, failure_store) -> None:
        for index in range(4):
            await failure_store.save_failure(
                FailureRecord(
                    project="axon",
                    operation=f"task-{index}",
                    error_message=f"failure {index}",
                    probable_cause="shared cause",
                    tags=["shared"],
                )
            )
        await failure_store.save_failure(
            FailureRecord(
                project="other",
                operation="other-task",
                error_message="other failure",
                probable_cause="other cause",
                tags=["shared"],
            )
        )

        failures = await failure_store.get_recent_failures("axon", limit=3)
        assert len(failures) == 3
        assert all(f.project == "axon" for f in failures)

    async def test_find_failures_by_tag_filters_project(self, failure_store) -> None:
        await failure_store.save_failure(
            FailureRecord(
                project="axon",
                operation="retrieve",
                error_message="timeout",
                probable_cause="network jitter",
                tags=["io", "retry"],
            )
        )
        await failure_store.save_failure(
            FailureRecord(
                project="other",
                operation="retrieve",
                error_message="timeout",
                probable_cause="network jitter",
                tags=["io", "retry"],
            )
        )

        failures = await failure_store.find_failures_by_tag("retry", project="axon")
        assert len(failures) == 1
        assert failures[0].project == "axon"

    async def test_get_repeated_failures_groups_by_probable_cause(self, failure_store) -> None:
        await failure_store.save_failure(
            FailureRecord(
                project="axon",
                operation="retrieve",
                error_message="timeout",
                probable_cause="network jitter",
                tags=["io"],
            )
        )
        await failure_store.save_failure(
            FailureRecord(
                project="axon",
                operation="compress",
                error_message="timeout",
                probable_cause="network jitter",
                tags=["io", "compression"],
            )
        )
        await failure_store.save_failure(
            FailureRecord(
                project="axon",
                operation="index",
                error_message="duplicate",
                probable_cause="bad dedupe config",
                tags=["indexing"],
            )
        )

        repeated = await failure_store.get_repeated_failures("axon", min_occurrences=2)
        assert repeated == [("network jitter", 2)]


@pytest.fixture
async def outcome_store(tmp_path) -> AsyncGenerator[OutcomeStore, None]:
    store = OutcomeStore(db_path=tmp_path / "outcome.db")
    await store.init()
    yield store
    await store.close()


@pytest.mark.asyncio
class TestOutcomeStore:
    async def test_save_and_get_outcomes_for_context(self, outcome_store) -> None:
        record = OutcomeRecord(
            project="axon",
            context="knowledge",
            summary="Java chunking fixture review prevented a bad merge",
            outcome="kept structure-aware chunking intact",
            tags=["chunker", "review"],
        )
        record_id = await outcome_store.save_outcome(record)

        outcomes = await outcome_store.get_outcomes_for_context("axon", "knowledge")
        assert record_id > 0
        assert len(outcomes) == 1
        assert outcomes[0].outcome == "kept structure-aware chunking intact"
        assert outcomes[0].tags == ["chunker", "review"]

    async def test_get_outcomes_for_context_filters_project(self, outcome_store) -> None:
        await outcome_store.save_outcome(
            OutcomeRecord(
                project="axon",
                context="knowledge",
                summary="kept fixture coverage stable",
                outcome="tests caught a parser regression",
                tags=["tests"],
            )
        )
        await outcome_store.save_outcome(
            OutcomeRecord(
                project="other",
                context="knowledge",
                summary="other project outcome",
                outcome="not relevant",
                tags=["tests"],
            )
        )

        outcomes = await outcome_store.get_outcomes_for_context("axon", "knowledge")
        assert len(outcomes) == 1
        assert outcomes[0].project == "axon"

    async def test_find_outcomes_by_tag_and_limit(self, outcome_store) -> None:
        for index in range(4):
            await outcome_store.save_outcome(
                OutcomeRecord(
                    project="axon",
                    context="saas",
                    summary=f"outcome {index}",
                    outcome=f"result {index}",
                    tags=["reuse", "playbook"],
                )
            )

        outcomes = await outcome_store.find_outcomes_by_tag(
            "playbook", project="axon", limit=2
        )
        assert len(outcomes) == 2
        assert all("playbook" in outcome.tags for outcome in outcomes)
