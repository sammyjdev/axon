"""Phase 0 GPU probe (v2): RTX 4070 Ti throughput via fastembed CUDA.

Calls onnxruntime.preload_dlls() so the CUDA EP finds the pip-installed
nvidia-cudnn-cu12 / nvidia-cublas-cu12 / nvidia-cuda-runtime-cu12 DLLs on
Windows. Verifies the live session actually bound CUDA (no silent CPU
fallback) and measures chunks/sec on the real axon corpus.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
out: dict = {}

import onnxruntime as ort  # noqa: E402

if hasattr(ort, "preload_dlls"):
    try:
        ort.preload_dlls()
        out["preload_dlls"] = "ok"
    except Exception as exc:  # noqa: BLE001
        out["preload_dlls_error"] = f"{type(exc).__name__}: {exc}"
else:
    out["preload_dlls"] = "not_available"

out["providers_available"] = ort.get_available_providers()

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

from fastembed import TextEmbedding  # noqa: E402

model = TextEmbedding(
    "BAAI/bge-base-en-v1.5",
    providers=["CUDAExecutionProvider", "CPUExecutionProvider"],
)
try:
    out["model_providers"] = model.model.model.get_providers()
except Exception as exc:  # noqa: BLE001
    out["model_providers"] = f"introspection_failed: {exc}"

list(model.embed(["warmup"] * 4, batch_size=4))

t = time.perf_counter()
n = sum(1 for _ in model.embed(texts, batch_size=128))
dt = time.perf_counter() - t
out["gpu_n"] = n
out["gpu_sec"] = round(dt, 2)
out["gpu_chunks_per_sec"] = round(n / dt) if dt else 0

(REPO / "benchmarks" / "phase0_gpu.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
