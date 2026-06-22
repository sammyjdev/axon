"""Diagnose why cuda=True fell back to CPU: list bge variants + force CUDA explicitly."""

from __future__ import annotations

import json
import time
from pathlib import Path

from fastembed import TextEmbedding

REPO = Path(__file__).resolve().parents[1]
out: dict = {}

variants = []
for m in TextEmbedding._list_supported_models() if hasattr(TextEmbedding, "_list_supported_models") else TextEmbedding.list_supported_models():
    name = m.get("model", "") if isinstance(m, dict) else getattr(m, "model", "")
    if "bge" in name.lower() and ("base-en" in name.lower() or "small-en" in name.lower()):
        variants.append(name)
out["bge_variants"] = variants


def try_model(name, providers):
    try:
        mdl = TextEmbedding(model_name=name, providers=providers)
        try:
            prov = mdl.model.model.get_providers()
        except Exception:  # noqa: BLE001
            prov = "introspection_failed"
        list(mdl.embed(["warmup"] * 4, batch_size=4))
        sample = ["def foo(x): return bar(x) + baz(x)"] * 512
        t = time.perf_counter()
        n = sum(1 for _ in mdl.embed(sample, batch_size=128))
        dt = time.perf_counter() - t
        return {"providers": prov, "n": n, "sec": round(dt, 2), "chunks_per_sec": round(n / dt) if dt else 0}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}


# the cached default (quantized) with EXPLICIT cuda
out["default_explicit_cuda"] = try_model(
    "BAAI/bge-base-en-v1.5", ["CUDAExecutionProvider", "CPUExecutionProvider"]
)

(REPO / "benchmarks" / "phase0_gpu_diag.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
