"""
T-041 — Chunker Java test suite.
Gate: ALL tests must pass before merging feat/phase-3a-chunker-java.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.embedder.chunker import Chunk, chunk_source

FIXTURES = Path(__file__).parent / "fixtures" / "spring"
PY_FIXTURES = Path(__file__).parent / "fixtures" / "python"
TS_FIXTURES = Path(__file__).parent / "fixtures" / "typescript"


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def load_py(name: str) -> str:
    return (PY_FIXTURES / name).read_text(encoding="utf-8")


def load_ts(name: str) -> str:
    return (TS_FIXTURES / name).read_text(encoding="utf-8")


def symbols(chunks: list[Chunk]) -> list[str]:
    return [c.symbol for c in chunks]


def chunk_types(chunks: list[Chunk]) -> list[str]:
    return [c.chunk_type for c in chunks]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def assert_no_orphans(chunks: list[Chunk]) -> None:
    for c in chunks:
        assert c.symbol not in ("", "anonymous"), (
            f"Orphaned chunk found: symbol={c.symbol!r}, type={c.chunk_type}, "
            f"lines {c.start_line}-{c.end_line}"
        )


def assert_valid_bounds(chunks: list[Chunk]) -> None:
    for c in chunks:
        assert c.start_line >= 1, f"start_line must be >= 1, got {c.start_line}"
        assert c.end_line >= c.start_line, (
            f"end_line {c.end_line} must be >= start_line {c.start_line}"
        )


def assert_content_nonempty(chunks: list[Chunk]) -> None:
    for c in chunks:
        assert c.content.strip(), f"Chunk {c.symbol!r} has empty content"


# ---------------------------------------------------------------------------
# SimpleController — 3 methods, no inner classes
# ---------------------------------------------------------------------------

class TestSimpleController:
    @pytest.fixture(autouse=True)
    def chunks(self):
        self._chunks = chunk_source(load("SimpleController.java"), "java", "SimpleController.java")

    def test_chunk_count(self):
        assert len(self._chunks) == 3

    def test_symbol_names(self):
        assert set(symbols(self._chunks)) == {"getOrder", "createOrder", "deleteOrder"}

    def test_all_method_type(self):
        assert all(c.chunk_type == "method" for c in self._chunks)

    def test_no_orphans(self):
        assert_no_orphans(self._chunks)

    def test_valid_bounds(self):
        assert_valid_bounds(self._chunks)

    def test_content_nonempty(self):
        assert_content_nonempty(self._chunks)

    def test_getOrder_start_line(self):
        chunk = next(c for c in self._chunks if c.symbol == "getOrder")
        assert chunk.start_line == 9  # annotation on line 9

    def test_getOrder_end_line(self):
        chunk = next(c for c in self._chunks if c.symbol == "getOrder")
        assert chunk.end_line == 12

    def test_createOrder_lines(self):
        chunk = next(c for c in self._chunks if c.symbol == "createOrder")
        assert chunk.start_line == 14
        assert chunk.end_line == 17

    def test_deleteOrder_lines(self):
        chunk = next(c for c in self._chunks if c.symbol == "deleteOrder")
        assert chunk.start_line == 19
        assert chunk.end_line == 22


# ---------------------------------------------------------------------------
# UserService — constructor + 4 methods
# ---------------------------------------------------------------------------

class TestUserService:
    @pytest.fixture(autouse=True)
    def chunks(self):
        self._chunks = chunk_source(load("UserService.java"), "java", "UserService.java")

    def test_chunk_count(self):
        # constructor + findById + create + update + delete
        assert len(self._chunks) == 5

    def test_has_constructor(self):
        assert any(c.chunk_type == "constructor" for c in self._chunks)

    def test_method_names(self):
        method_names = {c.symbol for c in self._chunks if c.chunk_type == "method"}
        assert method_names == {"findById", "create", "update", "delete"}

    def test_no_orphans(self):
        assert_no_orphans(self._chunks)

    def test_valid_bounds(self):
        assert_valid_bounds(self._chunks)


# ---------------------------------------------------------------------------
# OrderRepository — interface, methods from @Query
# ---------------------------------------------------------------------------

class TestOrderRepository:
    @pytest.fixture(autouse=True)
    def chunks(self):
        self._chunks = chunk_source(load("OrderRepository.java"), "java", "OrderRepository.java")

    def test_chunk_count(self):
        # 5 method declarations in the interface
        assert len(self._chunks) == 5

    def test_all_methods(self):
        assert all(c.chunk_type == "method" for c in self._chunks)

    def test_no_orphans(self):
        assert_no_orphans(self._chunks)

    def test_valid_bounds(self):
        assert_valid_bounds(self._chunks)


# ---------------------------------------------------------------------------
# PaymentService — methods + nested record (inner record should be 1 chunk)
# ---------------------------------------------------------------------------

class TestPaymentService:
    @pytest.fixture(autouse=True)
    def chunks(self):
        self._chunks = chunk_source(load("PaymentService.java"), "java", "PaymentService.java")

    def test_has_record_chunk(self):
        assert any(c.chunk_type == "record" for c in self._chunks)

    def test_record_symbol(self):
        records = [c for c in self._chunks if c.chunk_type == "record"]
        assert records[0].symbol == "PaymentResult"

    def test_method_count(self):
        methods = [c for c in self._chunks if c.chunk_type == "method"]
        assert len(methods) == 3

    def test_no_orphans(self):
        assert_no_orphans(self._chunks)

    def test_valid_bounds(self):
        assert_valid_bounds(self._chunks)


# ---------------------------------------------------------------------------
# NotificationService — anonymous classes inside methods are NOT separate chunks
# ---------------------------------------------------------------------------

class TestNotificationService:
    @pytest.fixture(autouse=True)
    def chunks(self):
        self._chunks = chunk_source(load("NotificationService.java"), "java", "NotificationService.java")

    def test_anonymous_classes_not_chunked(self):
        # anonymous Runnable impls must NOT appear as separate chunks
        for c in self._chunks:
            assert c.symbol not in ("Runnable", "anonymous"), (
                f"Anonymous class was incorrectly chunked as {c.symbol!r}"
            )

    def test_method_count(self):
        # constructor + sendAsync + sendOrderConfirmation + sendBulk + buildBody
        methods_and_ctors = [c for c in self._chunks if c.chunk_type in ("method", "constructor")]
        assert len(methods_and_ctors) == 5

    def test_no_orphans(self):
        assert_no_orphans(self._chunks)

    def test_valid_bounds(self):
        assert_valid_bounds(self._chunks)


# ---------------------------------------------------------------------------
# UserRecord — record with compact constructor + methods
# ---------------------------------------------------------------------------

class TestUserRecord:
    @pytest.fixture(autouse=True)
    def chunks(self):
        self._chunks = chunk_source(load("UserRecord.java"), "java", "UserRecord.java")

    def test_is_single_record_chunk(self):
        assert len(self._chunks) == 1
        assert self._chunks[0].chunk_type == "record"
        assert self._chunks[0].symbol == "UserRecord"

    def test_record_contains_compact_constructor(self):
        # compact constructor code must be inside the record chunk
        assert "name.isBlank()" in self._chunks[0].content

    def test_valid_bounds(self):
        assert_valid_bounds(self._chunks)


# ---------------------------------------------------------------------------
# OrderStatusEnum — enum with methods
# ---------------------------------------------------------------------------

class TestOrderStatusEnum:
    @pytest.fixture(autouse=True)
    def chunks(self):
        self._chunks = chunk_source(load("OrderStatusEnum.java"), "java", "OrderStatusEnum.java")

    def test_is_single_enum_chunk(self):
        assert len(self._chunks) == 1
        assert self._chunks[0].chunk_type == "enum"
        assert self._chunks[0].symbol == "OrderStatusEnum"

    def test_enum_contains_methods(self):
        content = self._chunks[0].content
        assert "canTransitionTo" in content
        assert "getLabel" in content

    def test_valid_bounds(self):
        assert_valid_bounds(self._chunks)


# ---------------------------------------------------------------------------
# SelfInvocationService — @Transactional with self-invocation (regular methods)
# ---------------------------------------------------------------------------

class TestSelfInvocationService:
    @pytest.fixture(autouse=True)
    def chunks(self):
        self._chunks = chunk_source(load("SelfInvocationService.java"), "java", "SelfInvocationService.java")

    def test_chunk_count(self):
        # constructor + processAll + processSingle + getItem
        assert len(self._chunks) == 4

    def test_has_all_methods(self):
        syms = set(symbols(self._chunks))
        assert {"processAll", "processSingle", "getItem"}.issubset(syms)

    def test_no_orphans(self):
        assert_no_orphans(self._chunks)

    def test_valid_bounds(self):
        assert_valid_bounds(self._chunks)


# ---------------------------------------------------------------------------
# CommandHandler — interface with default and static methods
# ---------------------------------------------------------------------------

class TestCommandHandler:
    @pytest.fixture(autouse=True)
    def chunks(self):
        self._chunks = chunk_source(load("CommandHandler.java"), "java", "CommandHandler.java")

    def test_method_count(self):
        # handle (abstract), handleWithLogging (default), withFallback (default), noOp (static)
        assert len(self._chunks) == 4

    def test_all_are_methods(self):
        assert all(c.chunk_type == "method" for c in self._chunks)

    def test_has_static_method(self):
        syms = symbols(self._chunks)
        assert "noOp" in syms

    def test_no_orphans(self):
        assert_no_orphans(self._chunks)

    def test_valid_bounds(self):
        assert_valid_bounds(self._chunks)


# ---------------------------------------------------------------------------
# GenericRepositoryService — methods with bounded types and wildcards
# ---------------------------------------------------------------------------

class TestGenericRepositoryService:
    @pytest.fixture(autouse=True)
    def chunks(self):
        self._chunks = chunk_source(load("GenericRepositoryService.java"), "java", "GenericRepositoryService.java")

    def test_chunk_count(self):
        assert len(self._chunks) == 4

    def test_generic_symbols(self):
        syms = set(symbols(self._chunks))
        assert {"saveWithAudit", "findMax", "findWithHighestScore", "deleteIfExists"} == syms

    def test_generic_content_preserved(self):
        save_chunk = next(c for c in self._chunks if c.symbol == "saveWithAudit")
        assert "extends Identifiable" in save_chunk.content

    def test_no_orphans(self):
        assert_no_orphans(self._chunks)

    def test_valid_bounds(self):
        assert_valid_bounds(self._chunks)


# ---------------------------------------------------------------------------
# StaticInnerClassService — static inner class becomes a single chunk
# ---------------------------------------------------------------------------

class TestStaticInnerClassService:
    @pytest.fixture(autouse=True)
    def chunks(self):
        self._chunks = chunk_source(load("StaticInnerClassService.java"), "java", "StaticInnerClassService.java")

    def test_has_inner_class_chunk(self):
        class_chunks = [c for c in self._chunks if c.chunk_type == "class"]
        assert len(class_chunks) == 1
        assert class_chunks[0].symbol == "Builder"

    def test_has_record_chunk(self):
        record_chunks = [c for c in self._chunks if c.chunk_type == "record"]
        assert len(record_chunks) == 1
        assert record_chunks[0].symbol == "Config"

    def test_outer_methods_are_chunks(self):
        method_chunks = [c for c in self._chunks if c.chunk_type == "method"]
        method_names = {c.symbol for c in method_chunks}
        assert {"buildConfig", "getPriority"}.issubset(method_names)

    def test_no_orphans(self):
        assert_no_orphans(self._chunks)


# ---------------------------------------------------------------------------
# NonStaticInnerClassService — non-static inner class becomes a chunk
# ---------------------------------------------------------------------------

class TestNonStaticInnerClassService:
    @pytest.fixture(autouse=True)
    def chunks(self):
        self._chunks = chunk_source(load("NonStaticInnerClassService.java"), "java", "NonStaticInnerClassService.java")

    def test_has_inner_class_chunk(self):
        class_chunks = [c for c in self._chunks if c.chunk_type == "class"]
        assert any(c.symbol == "Formatter" for c in class_chunks)

    def test_outer_methods(self):
        methods = [c for c in self._chunks if c.chunk_type == "method"]
        assert any(c.symbol == "formatValue" for c in methods)

    def test_no_orphans(self):
        assert_no_orphans(self._chunks)

    def test_valid_bounds(self):
        assert_valid_bounds(self._chunks)


# ---------------------------------------------------------------------------
# StreamProcessingService — complex Stream pipelines are part of method chunks
# ---------------------------------------------------------------------------

class TestStreamProcessingService:
    @pytest.fixture(autouse=True)
    def chunks(self):
        self._chunks = chunk_source(load("StreamProcessingService.java"), "java", "StreamProcessingService.java")

    def test_chunk_count(self):
        assert len(self._chunks) == 5

    def test_stream_code_in_method_chunks(self):
        group_chunk = next(c for c in self._chunks if c.symbol == "groupAndCountByCategory")
        assert "Collectors.groupingBy" in group_chunk.content
        assert "Collectors.counting" in group_chunk.content

    def test_lambdas_not_separate_chunks(self):
        syms = symbols(self._chunks)
        assert "anonymous" not in syms

    def test_no_orphans(self):
        assert_no_orphans(self._chunks)

    def test_valid_bounds(self):
        assert_valid_bounds(self._chunks)


# ---------------------------------------------------------------------------
# GlobalExceptionHandler — inner record + handler methods
# ---------------------------------------------------------------------------

class TestGlobalExceptionHandler:
    @pytest.fixture(autouse=True)
    def chunks(self):
        self._chunks = chunk_source(load("GlobalExceptionHandler.java"), "java", "GlobalExceptionHandler.java")

    def test_has_record_chunk(self):
        assert any(c.chunk_type == "record" and c.symbol == "ErrorResponse" for c in self._chunks)

    def test_has_handler_methods(self):
        methods = {c.symbol for c in self._chunks if c.chunk_type == "method"}
        assert {"handleNotFound", "handleBadRequest", "handleGeneral"}.issubset(methods)

    def test_no_orphans(self):
        assert_no_orphans(self._chunks)

    def test_valid_bounds(self):
        assert_valid_bounds(self._chunks)


# ---------------------------------------------------------------------------
# ScheduledTaskService — @Component with @Scheduled methods
# ---------------------------------------------------------------------------

class TestScheduledTaskService:
    @pytest.fixture(autouse=True)
    def chunks(self):
        self._chunks = chunk_source(load("ScheduledTaskService.java"), "java", "ScheduledTaskService.java")

    def test_chunk_count(self):
        # constructor + sendDailyReport + cleanupExpiredSessions + syncExternalData
        assert len(self._chunks) == 4

    def test_annotation_included_in_chunk(self):
        chunk = next(c for c in self._chunks if c.symbol == "sendDailyReport")
        assert "@Scheduled" in chunk.content

    def test_no_orphans(self):
        assert_no_orphans(self._chunks)

    def test_valid_bounds(self):
        assert_valid_bounds(self._chunks)


# ---------------------------------------------------------------------------
# OrderService — large 500+ line class, ~20 chunks
# ---------------------------------------------------------------------------

class TestOrderService:
    @pytest.fixture(autouse=True)
    def chunks(self):
        self._chunks = chunk_source(load("OrderService.java"), "java", "OrderService.java")

    def test_chunk_count_gte_15(self):
        assert len(self._chunks) >= 15

    def test_has_record_chunk(self):
        assert any(c.chunk_type == "record" and c.symbol == "OrderSummary" for c in self._chunks)

    def test_key_methods_present(self):
        syms = set(symbols(self._chunks))
        for expected in ("create", "findById", "processPayment", "fulfil", "ship", "deliver", "cancel"):
            assert expected in syms, f"Expected method '{expected}' not found in chunks"

    def test_private_helpers_chunked(self):
        syms = set(symbols(self._chunks))
        assert "validateItems" in syms
        assert "calculateTotal" in syms

    def test_no_orphans(self):
        assert_no_orphans(self._chunks)

    def test_valid_bounds(self):
        assert_valid_bounds(self._chunks)

    def test_chunks_cover_file(self):
        # all chunks together should span most of the file
        if not self._chunks:
            return
        last_line = max(c.end_line for c in self._chunks)
        source_lines = load("OrderService.java").count("\n")
        assert last_line >= source_lines * 0.8


# ---------------------------------------------------------------------------
# MultipleInnerClassService — 2 static inner classes
# ---------------------------------------------------------------------------

class TestMultipleInnerClassService:
    @pytest.fixture(autouse=True)
    def chunks(self):
        self._chunks = chunk_source(load("MultipleInnerClassService.java"), "java", "MultipleInnerClassService.java")

    def test_both_inner_classes_chunked(self):
        class_chunks = [c for c in self._chunks if c.chunk_type == "class"]
        names = {c.symbol for c in class_chunks}
        assert {"RequestValidator", "ResponseBuilder"}.issubset(names)

    def test_inner_record_chunk(self):
        assert any(c.chunk_type == "record" and c.symbol == "Response" for c in self._chunks)

    def test_outer_methods(self):
        methods = {c.symbol for c in self._chunks if c.chunk_type == "method"}
        assert {"process", "describe"}.issubset(methods)

    def test_no_orphans(self):
        assert_no_orphans(self._chunks)

    def test_valid_bounds(self):
        assert_valid_bounds(self._chunks)


# ---------------------------------------------------------------------------
# AbstractAuditService — abstract class with abstract + concrete methods
# ---------------------------------------------------------------------------

class TestAbstractAuditService:
    @pytest.fixture(autouse=True)
    def chunks(self):
        self._chunks = chunk_source(load("AbstractAuditService.java"), "java", "AbstractAuditService.java")

    def test_abstract_methods_are_chunked(self):
        syms = set(symbols(self._chunks))
        assert {"findById", "save"}.issubset(syms)

    def test_concrete_methods_are_chunked(self):
        syms = set(symbols(self._chunks))
        assert {"touchAndSave", "exists", "validateNotNull"}.issubset(syms)

    def test_no_orphans(self):
        assert_no_orphans(self._chunks)

    def test_valid_bounds(self):
        assert_valid_bounds(self._chunks)


# ---------------------------------------------------------------------------
# RecordWithCompactConstructor — complex record with methods
# ---------------------------------------------------------------------------

class TestRecordWithCompactConstructor:
    @pytest.fixture(autouse=True)
    def chunks(self):
        self._chunks = chunk_source(load("RecordWithCompactConstructor.java"), "java", "RecordWithCompactConstructor.java")

    def test_single_record_chunk(self):
        assert len(self._chunks) == 1
        assert self._chunks[0].chunk_type == "record"
        assert self._chunks[0].symbol == "RecordWithCompactConstructor"

    def test_compact_constructor_in_content(self):
        assert "publishedAt == null" in self._chunks[0].content

    def test_methods_in_content(self):
        content = self._chunks[0].content
        assert "isPublished" in content
        assert "summary" in content

    def test_valid_bounds(self):
        assert_valid_bounds(self._chunks)


# ---------------------------------------------------------------------------
# LambdaChainService — complex lambda chains are not separate chunks
# ---------------------------------------------------------------------------

class TestLambdaChainService:
    @pytest.fixture(autouse=True)
    def chunks(self):
        self._chunks = chunk_source(load("LambdaChainService.java"), "java", "LambdaChainService.java")

    def test_method_count(self):
        methods = [c for c in self._chunks if c.chunk_type == "method"]
        assert len(methods) == 3

    def test_has_record_chunk(self):
        assert any(c.chunk_type == "record" and c.symbol == "ReportLine" for c in self._chunks)

    def test_lambda_inside_method(self):
        report_chunk = next(c for c in self._chunks if c.symbol == "buildReport")
        assert "flatMap" in report_chunk.content
        assert "Collectors.toList" in report_chunk.content

    def test_no_orphans(self):
        assert_no_orphans(self._chunks)

    def test_valid_bounds(self):
        assert_valid_bounds(self._chunks)


# ---------------------------------------------------------------------------
# TransactionalService — multiple @Transactional propagations
# ---------------------------------------------------------------------------

class TestTransactionalService:
    @pytest.fixture(autouse=True)
    def chunks(self):
        self._chunks = chunk_source(load("TransactionalService.java"), "java", "TransactionalService.java")

    def test_method_count(self):
        # constructor + transfer + getBalance + auditTransfer + reserveFunds
        assert len(self._chunks) == 5

    def test_all_methods_present(self):
        syms = set(symbols(self._chunks))
        assert {"transfer", "getBalance", "auditTransfer", "reserveFunds"}.issubset(syms)

    def test_no_orphans(self):
        assert_no_orphans(self._chunks)

    def test_valid_bounds(self):
        assert_valid_bounds(self._chunks)


# ---------------------------------------------------------------------------
# WildcardService — methods with wildcard generic parameters
# ---------------------------------------------------------------------------

class TestWildcardService:
    @pytest.fixture(autouse=True)
    def chunks(self):
        self._chunks = chunk_source(load("WildcardService.java"), "java", "WildcardService.java")

    def test_chunk_count(self):
        assert len(self._chunks) == 4

    def test_wildcard_content_preserved(self):
        chunk = next(c for c in self._chunks if c.symbol == "sumNumbers")
        assert "? extends Number" in chunk.content

    def test_no_orphans(self):
        assert_no_orphans(self._chunks)

    def test_valid_bounds(self):
        assert_valid_bounds(self._chunks)


# ===========================================================================
# Python fixtures (T-050) — 10 files, structure validation
# ===========================================================================

class TestPythonChunker:
    def _chunks(self, name: str) -> list[Chunk]:
        return chunk_source(load_py(name), "python", name)

    def test_simple_service_top_level_functions(self):
        chunks = self._chunks("simple_service.py")
        syms = set(symbols(chunks))
        assert {"find_by_id", "filter_by_price"}.issubset(syms)

    def test_simple_service_class_methods(self):
        chunks = self._chunks("simple_service.py")
        syms = set(symbols(chunks))
        assert {"get", "add", "remove", "total_value"}.issubset(syms)

    def test_simple_service_no_class_level_chunk(self):
        chunks = self._chunks("simple_service.py")
        class_chunks = [c for c in chunks if c.chunk_type == "class"]
        assert len(class_chunks) == 0, "Top-level class should not appear as a chunk"

    def test_vector_store_methods(self):
        chunks = self._chunks("vector_store.py")
        syms = set(symbols(chunks))
        assert {"search", "upsert", "delete"}.issubset(syms)

    def test_vector_store_private_method(self):
        chunks = self._chunks("vector_store.py")
        assert any(c.symbol == "_ensure_client" for c in chunks)

    def test_session_store_methods(self):
        chunks = self._chunks("session_store.py")
        syms = set(symbols(chunks))
        assert {"save", "load_recent", "last_context"}.issubset(syms)

    def test_top_level_function_not_orphan(self):
        chunks = self._chunks("session_store.py")
        assert any(c.symbol == "format_entry" and c.chunk_type == "function" for c in chunks)

    def test_router_functions(self):
        chunks = self._chunks("router.py")
        syms = set(symbols(chunks))
        assert {"route", "_is_architecture_query", "estimate_tokens"}.issubset(syms)

    def test_no_orphans_python(self):
        for name in ["simple_service.py", "vector_store.py", "context_detector.py",
                     "router.py", "session_store.py", "chunker_utils.py",
                     "graph_service.py", "til_promoter.py", "adr_manager.py", "embedder_engine.py"]:
            chunks = self._chunks(name)
            assert_no_orphans(chunks)

    def test_valid_bounds_python(self):
        for name in ["simple_service.py", "vector_store.py", "context_detector.py",
                     "router.py", "session_store.py", "chunker_utils.py",
                     "graph_service.py", "til_promoter.py", "adr_manager.py", "embedder_engine.py"]:
            chunks = self._chunks(name)
            assert_valid_bounds(chunks)


# ===========================================================================
# TypeScript fixtures (T-050) — 10 files, structure validation
# ===========================================================================

class TestTypescriptChunker:
    def _chunks(self, name: str) -> list[Chunk]:
        return chunk_source(load_ts(name), "typescript", name)

    def test_api_client_methods(self):
        chunks = self._chunks("api_client.ts")
        syms = set(symbols(chunks))
        assert {"search", "saveAdr", "getMemory"}.issubset(syms)

    def test_context_detector_class(self):
        chunks = self._chunks("context_detector.ts")
        syms = set(symbols(chunks))
        assert "detect" in syms

    def test_session_manager_methods(self):
        chunks = self._chunks("session_manager.ts")
        syms = set(symbols(chunks))
        assert {"addEntry", "getRecent", "formatSummary", "setProject"}.issubset(syms)

    def test_top_level_functions_ts(self):
        chunks = self._chunks("router.ts")
        syms = set(symbols(chunks))
        assert {"classifyQuery", "route"}.issubset(syms)

    def test_no_orphans_typescript(self):
        for name in ["api_client.ts", "context_detector.ts", "session_manager.ts",
                     "router.ts", "vault_client.ts", "mcp_tools.ts",
                     "collections.ts", "chunker.ts", "platform_utils.ts", "cost_tracker.ts"]:
            chunks = self._chunks(name)
            assert_no_orphans(chunks)

    def test_valid_bounds_typescript(self):
        for name in ["api_client.ts", "context_detector.ts", "session_manager.ts",
                     "router.ts", "vault_client.ts", "mcp_tools.ts",
                     "collections.ts", "chunker.ts", "platform_utils.ts", "cost_tracker.ts"]:
            chunks = self._chunks(name)
            assert_valid_bounds(chunks)
