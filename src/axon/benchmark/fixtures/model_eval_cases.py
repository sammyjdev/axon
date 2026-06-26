"""Gold cases for the local-model comparison benchmark.

Scoring cases carry the expected decision (derived from curated scores in
production); compressor cases carry the symbols that must survive compression.
Mixed EN/PT to exercise the Portuguese reasoning path the scoring prompt uses.
"""

from __future__ import annotations

from axon.benchmark.model_eval import CompressorEvalCase, ScoringEvalCase
from axon.expansion.scoring import ExpansionCandidate, ExpansionDecision

SCORING_CASES: tuple[ScoringEvalCase, ...] = (
    ScoringEvalCase(
        candidate=ExpansionCandidate(
            title="async-profiler flamegraphs for JVM hotspots",
            extracted_text=(
                "async-profiler attaches to a running JVM and samples CPU stacks "
                "with low overhead. Render the collapsed stacks as a flamegraph to "
                "find the hottest call paths, then optimise the widest frames first."
            ),
            source_url="https://example.com/async-profiler",
        ),
        topic="java cpu profiling",
        gold_decision=ExpansionDecision.KEEP,
    ),
    ScoringEvalCase(
        candidate=ExpansionCandidate(
            title="Weekend travel packing checklist",
            extracted_text=(
                "Pack two shirts, a charger, sunscreen and a reusable water bottle. "
                "Check the weather the night before and leave early to beat traffic."
            ),
            source_url="https://example.com/packing",
        ),
        topic="java cpu profiling",
        gold_decision=ExpansionDecision.DISCARD,
    ),
    ScoringEvalCase(
        candidate=ExpansionCandidate(
            title="JVM profiling mentioned in passing",
            extracted_text=(
                "Profiling the JVM can help with performance. There are several tools "
                "available and teams should consider using them when relevant."
            ),
            source_url="https://example.com/thin",
        ),
        topic="java cpu profiling",
        gold_decision=ExpansionDecision.MAYBE,
    ),
    ScoringEvalCase(
        candidate=ExpansionCandidate(
            title="Indexação incremental com pgvector",
            extracted_text=(
                "Para reduzir a latência de recall, crie um índice HNSW em pgvector "
                "com lists ajustadas ao volume e rode ANALYZE após a carga inicial. "
                "Reindexe de forma incremental conforme novos embeddings chegam."
            ),
            source_url="https://example.com/pgvector-hnsw",
        ),
        topic="otimização de busca vetorial em postgres",
        gold_decision=ExpansionDecision.KEEP,
    ),
)

COMPRESSOR_CASES: tuple[CompressorEvalCase, ...] = (
    CompressorEvalCase(
        text=(
            "The service exposes recall_context(query, k) which calls the VectorStore "
            "repository and returns ranked decisions. On a miss it raises "
            "RECALL_EMPTY and the caller must fall back to keyword search. The "
            "MAX_RESULTS constant caps the page at fifty rows."
        ),
        required_symbols=("recall_context", "VectorStore", "RECALL_EMPTY", "MAX_RESULTS"),
    ),
    CompressorEvalCase(
        text=(
            "A função caveman_compress(text, max_tokens) remove filler mas preserva "
            "assinaturas e regras. Se a confiança cair abaixo de MIN_CONFIDENCE, ela "
            "retorna o texto original e registra o erro via logger.warning. Nunca "
            "deve levantar exceção para o chamador."
        ),
        required_symbols=("caveman_compress", "MIN_CONFIDENCE", "logger.warning"),
    ),
    CompressorEvalCase(
        text=(
            "Decision dec-106 makes Ollama opt-in via AXON_PROVIDER_OLLAMA. The router "
            "resolves a handle to either an anthropic backend or local_m1, and on a "
            "schema-invalid response it downgrades to the fallback model named on the "
            "handle. Error code ROUTE_DEGRADED is emitted for observability."
        ),
        required_symbols=("dec-106", "AXON_PROVIDER_OLLAMA", "local_m1", "ROUTE_DEGRADED"),
    ),
)
