from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from axon.benchmark.contracts import BenchmarkCheck, BenchmarkResult, BenchmarkRunSummary

GOLDEN_SET_PATH = Path(__file__).parent / "golden_set.json"
BASELINE_PATH = Path(__file__).parent / "baseline.json"

REQUIRED_QUERY_KEYS = {"id", "query", "expected_file", "expected_symbol", "min_score"}


def test_golden_set_schema() -> None:
    """Golden set file exists and each entry has all required keys."""
    assert GOLDEN_SET_PATH.exists(), "golden_set.json missing"
    data = json.loads(GOLDEN_SET_PATH.read_text(encoding="utf-8"))
    assert len(data) == 20, f"expected 20 queries, got {len(data)}"
    for entry in data:
        missing = REQUIRED_QUERY_KEYS - set(entry.keys())
        assert not missing, f"entry {entry.get('id')} missing keys: {missing}"
        assert isinstance(entry["min_score"], float)
        assert 0.0 < entry["min_score"] <= 1.0


def test_golden_set_no_duplicate_ids() -> None:
    data = json.loads(GOLDEN_SET_PATH.read_text(encoding="utf-8"))
    ids = [e["id"] for e in data]
    assert len(ids) == len(set(ids)), "duplicate IDs in golden_set.json"


def test_baseline_json_exists() -> None:
    assert BASELINE_PATH.exists(), "baseline.json missing"
    data = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    assert "recall_top1" in data
    assert "recall_top3" in data
    assert "results_by_query" in data


def test_recall_guard_harness_infrastructure() -> None:
    """Offline mock test: verify BenchmarkRunSummary score math for worst and perfect cases."""
    # Worst case: all checks fail -> score 0.0
    worst_check = BenchmarkCheck(
        name="hit_top1",
        passed=False,
        expected="rank=1",
        actual="rank=none",
    )
    worst_result = BenchmarkResult(
        suite="recall_guard",
        name="q_worst",
        duration_ms=1.0,
        checks=(worst_check,),
    )
    worst_summary = BenchmarkRunSummary(results=(worst_result,))
    assert worst_summary.score == 0.0

    # Perfect case: all checks pass -> score 1.0
    perfect_check = BenchmarkCheck(
        name="hit_top1",
        passed=True,
        expected="rank=1",
        actual="rank=1 top_score=0.9500",
    )
    perfect_result = BenchmarkResult(
        suite="recall_guard",
        name="q_perfect",
        duration_ms=1.0,
        checks=(perfect_check,),
    )
    perfect_summary = BenchmarkRunSummary(results=(perfect_result,))
    assert perfect_summary.score == 1.0

    # Mixed: one pass, one fail -> score 0.5
    mixed_summary = BenchmarkRunSummary(results=(worst_result, perfect_result))
    assert mixed_summary.score == 0.5

    # Verify mock engine + client integration shape (no real GPU/Qdrant)
    mock_engine = MagicMock()
    mock_engine.embed_one.return_value = [0.0] * 768

    mock_hit = MagicMock()
    mock_hit.score = 0.95
    mock_hit.payload = {
        "file_path": "src/axon/embedder/chunker.py",
        "symbol": "_walk_python",
        "chunk_type": "function",
    }

    mock_points_result = MagicMock()
    mock_points_result.points = [mock_hit]

    mock_client = MagicMock()
    mock_client.query_points.return_value = mock_points_result

    from axon.benchmark.recall import run_recall_guard

    golden_subset = [
        {
            "id": "q01",
            "query": "chunk python source tree-sitter walk function definition",
            "expected_file": "src/axon/embedder/chunker.py",
            "expected_symbol": "_walk_python",
            "min_score": 0.70,
        }
    ]
    summary, metrics = run_recall_guard(golden_subset, mock_engine, mock_client)
    assert summary.score == 1.0
    assert metrics["recall_top1"] == 1.0
    assert metrics["recall_top3"] == 1.0


@pytest.mark.skipif(
    os.environ.get("AXON_RUN_RECALL") != "1",
    reason="set AXON_RUN_RECALL=1 to run the GPU+Qdrant recall gate",
)
def test_recall_guard_no_regression() -> None:
    """Real GPU+Qdrant gate: asserts recall_top1 >= 0.90 and no per-query regressions."""
    import json
    from pathlib import Path

    from qdrant_client import QdrantClient

    from axon.benchmark.recall import TEMP_COLLECTION, index_corpus, run_recall_guard
    from axon.embedder.engine import EmbedderEngine

    repo_root = Path(__file__).resolve().parent.parent.parent
    src_root = repo_root / "src" / "axon"
    golden_set = json.loads(GOLDEN_SET_PATH.read_text(encoding="utf-8"))
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))

    engine = EmbedderEngine()
    client = QdrantClient(url="http://localhost:6333")

    try:
        index_corpus(client, engine, src_root=src_root, repo_root=repo_root)
        _summary, metrics = run_recall_guard(golden_set, engine, client)
    finally:
        try:
            client.delete_collection(collection_name=TEMP_COLLECTION)
        except Exception:  # noqa: BLE001
            pass

    recall_top1 = metrics["recall_top1"]
    assert recall_top1 >= 0.90, (
        f"recall_top1={recall_top1:.3f} is below the 0.90 gate. "
        "Missed queries: "
        + str([qid for qid, r in metrics["results_by_query"].items() if not r["hit_top1"]])
    )

    baseline_results = baseline.get("results_by_query", {})
    regressions = []
    for qid, current in metrics["results_by_query"].items():
        if qid not in baseline_results:
            continue
        baseline_rank_raw = baseline_results[qid].get("rank")
        # "none" or null -> treat as worst (6, beyond top_k=5)
        if baseline_rank_raw is None or baseline_rank_raw == "none":
            baseline_rank = 6
        else:
            baseline_rank = int(baseline_rank_raw)
        current_rank = current["rank"] if current["rank"] is not None else 6
        if current_rank > baseline_rank:
            regressions.append(
                f"{qid}: baseline rank={baseline_rank} -> current rank={current_rank}"
            )

    assert not regressions, "Per-query rank regressions detected:\n" + "\n".join(regressions)
