"""Public snapshot of recall savings telemetry (README "real-usage savings").

The raw chunks.jsonl carries absolute vault/repo paths and must never ship.
The snapshot is the privacy boundary: whitelisted integer counts + opaque ids
only, and the aggregate must be recomputable from the snapshot alone so the
public claim is reproducible without local data.
"""

from __future__ import annotations

import json
from pathlib import Path

from axon.observability.savings import (
    SNAPSHOT_FIELDS,
    aggregate_recall_savings,
    aggregate_snapshot,
    export_savings_snapshot,
)


def _write_record(chunks_file: Path, *, query_hash: str, chunks: list[dict[str, object]]) -> None:
    with chunks_file.open("a", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "ts": "2026-07-03T05:17:19+00:00",
                    "query_hash": query_hash,
                    "strategy": "balanced",
                    "requested_max_tokens": 2000,
                    "chunks": chunks,
                }
            )
            + "\n"
        )


def _fixture(tmp_path: Path) -> Path:
    src = tmp_path / "secret_module.py"
    src.write_text("x" * 40, encoding="utf-8")
    chunks_file = tmp_path / "chunks.jsonl"
    _write_record(
        chunks_file,
        query_hash="how do I configure the private thing",
        chunks=[{"hash": "a1", "file_path": str(src), "token_estimate": 6}],
    )
    _write_record(chunks_file, query_hash="pre-t8", chunks=[{"hash": "b1", "token_estimate": 9}])
    return chunks_file


def test_snapshot_rows_carry_only_whitelisted_fields(tmp_path: Path) -> None:
    rows = export_savings_snapshot(_fixture(tmp_path))

    assert len(rows) == 1
    assert set(rows[0]) == SNAPSHOT_FIELDS
    assert rows[0]["date"] == "2026-07-03"
    assert rows[0]["returned_tokens"] == 6
    assert rows[0]["counterfactual_tokens"] == 10
    assert rows[0]["missing_files"] == 0


def test_snapshot_leaks_no_paths_and_ids_are_unlinkable(tmp_path: Path) -> None:
    chunks_file = _fixture(tmp_path)
    rows_a = export_savings_snapshot(chunks_file)
    rows_b = export_savings_snapshot(chunks_file)

    for row in rows_a:
        for value in row.values():
            text = str(value)
            assert "/" not in text and "\\" not in text
            assert "secret_module" not in text
        assert row["id"] != "how do I configure the private thing"
    # fresh random salt per export: ids must not be linkable across exports
    assert rows_a[0]["id"] != rows_b[0]["id"]


def test_aggregate_from_snapshot_matches_raw_aggregate(tmp_path: Path) -> None:
    chunks_file = _fixture(tmp_path)
    raw = aggregate_recall_savings(chunks_file)

    snapshot_file = tmp_path / "snapshot.jsonl"
    with snapshot_file.open("w", encoding="utf-8") as fh:
        for row in export_savings_snapshot(chunks_file):
            fh.write(json.dumps(row) + "\n")

    snap = aggregate_snapshot(snapshot_file)

    assert snap.requests == raw.requests
    assert snap.returned_tokens == raw.returned_tokens
    assert snap.counterfactual_tokens == raw.counterfactual_tokens
    assert snap.savings_ratio == raw.savings_ratio
