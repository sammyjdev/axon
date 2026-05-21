# Migration plan — `@dataclass` → Pydantic v2

Status: audit (Phase 0.4). Informs dec-105. No code changed by this document.

## Headline finding

The original AXON draft assumed ~9 domain models. The codebase actually has
**71 `@dataclass` declarations**. They are **not** all data models — a
significant share are internal value objects, config objects, or service
classes. "Migrate everything" is therefore the wrong instruction: migrating a
service class (e.g. `EmbedderEngine`) to `pydantic.BaseModel` is an anti-pattern.

The 71 are classified into three buckets below. **Only bucket A migrates.**

## Bucket A — data models → migrate to Pydantic v2 (~28)

Persisted to SQLite/Qdrant/Redis, or serialized to JSON/markdown — they cross a
process boundary and benefit from validation + round-trip safety.

| Module | Models |
| --- | --- |
| `store/session_store.py` | `ADR`, `SessionMemory`, `SessionNote`, `CodeChange` |
| `store/failure_store.py` | `FailureRecord` |
| `store/outcome_store.py` | `OutcomeRecord` |
| `store/vector_store.py` | `Chunk` |
| `embedder/chunker.py` | `Chunk` ⚠️ **D5 gate** |
| `embedder/graph_extractor.py` | `DependencyRecord` |
| `config/projects.py` | `ProjectEntry` |
| `expansion/models.py` | `JsonFieldMap`, `SourceDefinition`, `SourceResponse`, `SourceDocument` |
| `expansion/staging.py` | `StagedSource`, `ExpansionDraft` |
| `expansion/budget.py` | `BudgetUsageRecord` |
| `expansion/telemetry.py` | `ExpansionExecutionRecord` |
| `observability/compression_telemetry.py` | `CompressionRecord` |
| `observability/compliance.py` | `ComplianceEvent` |
| `observability/trace_store.py` | `TraceRecord` |
| `portability/exporter.py` | `ExportArtifact`, `ExportManifest` |
| `registry/contracts.py` | `PluginManifest`, `ToolDescriptor` |
| `domains/pack.py` | `DomainSignals`, `DomainPackExample`, `DomainPackManifest` |

> ⚠️ **D5 gate:** `embedder/chunker.py:Chunk` is consumed by the 118-assertion
> Java chunker suite. Migration rule (dec-105): preserve every assertion; only
> mechanical constructor adaptation and `asdict` → `model_dump` are allowed.

## Bucket B — internal value/config objects → keep `@dataclass` (~37)

In-process only; mostly `frozen=True`. No serialization boundary.

- `benchmark/*` — `BenchmarkCase`, `BenchmarkCheck`, `BenchmarkResult`,
  `BenchmarkRunSummary`, `BenchmarkComparisonEntry`, `BenchmarkComparisonReport`,
  `SetupModeBenchmarkCase/Fixture`, `CompressionFallbackBenchmarkFixture`,
  `RetrievalExpectation`, `RetrievalBenchmarkFixture` (11)
- `config/runtime.py` — `ExpansionPaths`, `ExpansionBudgetConfig`,
  `ExpansionConfig`, `RuntimeConfig`, `CapabilitySelection` (5)
- `config/platform.py` — `PlatformConfig`, `DoctorReport`, `SetupPlan` (3)
- `context/*` — `ContextResult`, `RetrievalStrategy`, `ContextPack`,
  `StalenessAssessment`, `StaleReplacement`, `CompressionConfidence` (6)
- `router/engine.py` — `TaskRequest`, `RouteResult` (2)
- `policy/core.py` — `PolicyDecision` (1)
- `expansion/scoring.py` — `ExpansionCandidate`, `ExpansionScore`,
  `ExpansionScoreResult` (3); `expansion/service.py` — `ReviewGate` (1);
  `expansion/budget.py` — `ExpansionBudgetStatus` (1)
- `memory/config.py` — `Mem0Config` (1); `resilience/circuit_breaker.py` —
  `_Snapshot` (1)

## Bucket C — service classes → keep `@dataclass`, never migrate (~8)

These hold behavior, not data. `@dataclass` is used only for `__init__`
convenience. Migrating to `BaseModel` is an anti-pattern.

`memory/session_compressor.py:SessionCompressor`,
`watcher/main.py:VaultWatcher`, `embedder/engine.py:EmbedderEngine`,
`cli/setup_session.py:SetupSession`, `registry/local.py:LocalRegistry`,
`expansion/registry.py:SourceRegistry`, `expansion/collector.py:ExpansionCollector`.

## Migration order (Phase 1, T1.2)

1. New core models first (`Decision`, `Symbol`, `Edge`) — Pydantic, greenfield.
2. `store/*` models (ADR, SessionMemory, SessionNote, CodeChange, FailureRecord,
   OutcomeRecord, Chunk) — they have the densest test coverage; do them early.
3. `embedder/chunker.py:Chunk` under the D5 gate — review 118 assertions.
4. Remaining bucket A modules.
5. Update CLAUDE.md convention text per dec-105.

## Per-model migration pattern

Replace `@dataclass` with `BaseModel`; move `__post_init__` timestamp logic to
`Field(default_factory=...)` or a `model_validator`; keep field names identical;
replace `dataclasses.asdict()/astuple()` with `model_dump()`.
