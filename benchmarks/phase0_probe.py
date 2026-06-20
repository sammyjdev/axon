"""Phase 0 LIGHT probes for the indexing perf overhaul.

Safe to run: does NOT load the embedding model (no EmbedderEngine / no
TextEmbedding() call), so there is no RSS blow-up risk. Pure introspection +
tree-sitter chunking + git/Qdrant metadata queries.

Writes benchmarks/phase0_probe.json and prints a summary to stderr.
"""

from __future__ import annotations

import inspect
import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
out: dict = {}


def section(name, fn):
    try:
        fn()
    except Exception as exc:  # noqa: BLE001 - probe must be resilient
        out[f"{name}_error"] = f"{type(exc).__name__}: {exc}"


def _ort():
    import onnxruntime as ort

    out["ort_providers"] = ort.get_available_providers()
    out["ort_device"] = ort.get_device()
    so = ort.SessionOptions()
    out["ort_default_intra_threads"] = so.intra_op_num_threads
    out["ort_default_inter_threads"] = so.inter_op_num_threads


def _fastembed():
    import fastembed
    from fastembed import TextEmbedding

    out["fastembed_version"] = getattr(fastembed, "__version__", "unknown")
    sig = inspect.signature(TextEmbedding.__init__)
    out["fastembed_init_params"] = list(sig.parameters.keys())
    out["fastembed_has_providers_kwarg"] = "providers" in sig.parameters


def _walk():
    t = time.perf_counter()
    rglob_entries = sum(1 for _ in REPO.rglob("*"))
    out["rglob_total_entries"] = rglob_entries
    out["rglob_wall_sec"] = round(time.perf_counter() - t, 3)

    t = time.perf_counter()
    r = subprocess.run(
        ["git", "-C", str(REPO), "ls-files", "--cached", "--exclude-standard"],
        capture_output=True,
        text=True,
        check=True,
    )
    git_files = [ln for ln in r.stdout.splitlines() if ln]
    out["gitlsfiles_count"] = len(git_files)
    out["gitlsfiles_wall_sec"] = round(time.perf_counter() - t, 3)


def _chunks():
    from axon.embedder.chunker import chunk_source
    from axon.embedder.pipeline import _LANGUAGE_MAP, _chunk_id, iter_supported_files

    lens: list[int] = []
    big = 0
    ids: dict[str, list] = {}
    n = 0
    for f in iter_supported_files(REPO):
        lang = _LANGUAGE_MAP.get(f.suffix)
        if lang is None:
            continue
        try:
            src = f.read_text(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            continue
        for c in chunk_source(src, lang, str(f)):
            n += 1
            length = len(c.content)
            lens.append(length)
            if length > 2000:
                big += 1
            cid = _chunk_id(f, c)
            ids.setdefault(cid, []).append((str(f), c.symbol, c.start_line))

    lens.sort()

    def pct(p):
        return lens[min(len(lens) - 1, int(len(lens) * p))] if lens else 0

    out["chunk_total"] = n
    out["chunk_big_gt2000chars"] = big
    out["chunk_big_pct"] = round(100 * big / n, 1) if n else 0
    out["chunk_p50_chars"] = pct(0.5)
    out["chunk_p90_chars"] = pct(0.9)
    out["chunk_p99_chars"] = pct(0.99)
    out["chunk_max_chars"] = lens[-1] if lens else 0
    collisions = {k: v for k, v in ids.items() if len(v) > 1}
    out["chunkid_collisions"] = len(collisions)
    out["chunkid_collision_samples"] = list(collisions.values())[:3]


def _qdrant():
    from qdrant_client import QdrantClient

    qc = QdrantClient("http://127.0.0.1:6333", check_compatibility=False)
    counts = {}
    for col in qc.get_collections().collections:
        counts[col.name] = qc.count(col.name).count
    out["qdrant_points"] = counts


section("ort", _ort)
section("fastembed", _fastembed)
section("walk", _walk)
section("chunks", _chunks)
section("qdrant", _qdrant)

(REPO / "benchmarks").mkdir(exist_ok=True)
(REPO / "benchmarks" / "phase0_probe.json").write_text(
    json.dumps(out, indent=2), encoding="utf-8"
)
print(json.dumps(out, indent=2), file=sys.stderr)
