"""Phase 0 HEAVY probe: throughput + RSS, memory-capped.

Safe by construction: small batch sizes + a hard 6 GB RSS abort, so it does
NOT reproduce the prior ~14 GB blow-up (which needs batch=400 x long
sequences). It isolates where RSS comes from (chunk list vs model vs embed)
and measures small-chunk vs big-chunk throughput on an idle machine.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import psutil

REPO = Path(__file__).resolve().parents[1]
PROC = psutil.Process()
CAP_MB = 6000


def rss_mb() -> int:
    return round(PROC.memory_info().rss / 1024 / 1024)


out: dict = {"rss_start_mb": rss_mb()}

from axon.embedder.chunker import chunk_source  # noqa: E402
from axon.embedder.pipeline import _LANGUAGE_MAP, iter_supported_files  # noqa: E402

chunks = []
for f in iter_supported_files(REPO):
    lang = _LANGUAGE_MAP.get(f.suffix)
    if lang is None:
        continue
    try:
        src = f.read_text(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        continue
    chunks.extend(chunk_source(src, lang, str(f)))

texts = [c.content for c in chunks]
out["n_chunks"] = len(texts)
# RSS after holding ALL chunk objects + text in memory == the graph_chunks list cost
out["rss_after_chunk_list_mb"] = rss_mb()

from axon.embedder.engine import EmbedderEngine  # noqa: E402

eng = EmbedderEngine()
t = time.perf_counter()
eng.embed(["warmup"])
out["model_load_sec"] = round(time.perf_counter() - t, 2)
out["rss_after_model_load_mb"] = rss_mb()

small = [t for t in texts if len(t) <= 2000]
big = [t for t in texts if len(t) > 2000]
out["n_small"] = len(small)
out["n_big"] = len(big)

peak = rss_mb()


def embed_set(items, batch_size, label):
    global peak
    start = time.perf_counter()
    done = 0
    for i in range(0, len(items), batch_size):
        eng.embed(items[i : i + batch_size])
        done += len(items[i : i + batch_size])
        r = rss_mb()
        peak = max(peak, r)
        if r > CAP_MB:
            return {"aborted_at_rss_mb": r, "done": done}
    dt = time.perf_counter() - start
    return {"n": done, "sec": round(dt, 1), "chunks_per_sec": round(done / dt) if dt else 0}


# cap the small set to keep it quick; embed ALL big chunks (they are the risk)
out["small_result"] = embed_set(small[:800], 64, "small")
out["rss_after_small_mb"] = rss_mb()
out["big_result"] = embed_set(big, 8, "big")
out["rss_after_big_mb"] = rss_mb()
out["peak_rss_mb"] = peak

(REPO / "benchmarks" / "phase0_heavy.json").write_text(
    json.dumps(out, indent=2), encoding="utf-8"
)
print(json.dumps(out, indent=2), file=sys.stderr)
