# GPU Setup for AXON Embedding Acceleration

Per-machine embedding dependencies. **DO NOT add these to `pyproject.toml`** -
they are machine-specific (a CUDA wheel installed on a Mac, or vice versa,
breaks `import onnxruntime`). Install them directly into the active venv on
each machine as documented below.

The engine auto-selects the execution provider via
`axon.embedder.engine._detect_providers()` (priority: CUDA -> CoreML -> CPU,
CUDA wins globally). `onnxruntime.preload_dlls()` is called at engine import
time so pip-installed NVIDIA DLLs are on the search path before any ONNX
session is created.

## CUDA Desktop (RTX 4070 Ti - Windows, confirmed in Phase 0: 541 chunks/s)

```
pip install onnxruntime-gpu==1.26.0 nvidia-cudnn-cu12 nvidia-cublas-cu12 nvidia-cuda-runtime-cu12
```

Verification (run in project root):

```
python -c "
import onnxruntime as ort
if hasattr(ort, 'preload_dlls'):
    ort.preload_dlls()
print('available:', ort.get_available_providers())
from axon.embedder.engine import _detect_providers
print('selected :', _detect_providers())
"
```

Expected: `available` includes `CUDAExecutionProvider` and `selected` is
`['CUDAExecutionProvider', 'CPUExecutionProvider']`. If `CUDAExecutionProvider`
is absent, the usual cause is missing `nvidia-*` packages (re-run the pip
install above). Silent CPU fallback is the failure mode to watch for - see the
bound-provider check below.

Bound-provider verification (catches a silent CPU fallback inside fastembed):

```
python -c "
from fastembed import TextEmbedding
m = TextEmbedding('BAAI/bge-base-en-v1.5', providers=['CUDAExecutionProvider','CPUExecutionProvider'])
print(m.model.model.get_providers())
"
```

Expected: `['CUDAExecutionProvider', 'CPUExecutionProvider']`. If it prints only
`['CPUExecutionProvider']`, the model fell back to CPU despite the request -
re-check the `nvidia-*` packages and CUDA runtime.

## Apple Silicon Mac (M1 Pro - not yet measured)

```
pip install onnxruntime
```

Standard `onnxruntime` ships `CoreMLExecutionProvider` on Darwin arm64; no extra
packages are required. `_detect_providers()` returns
`['CoreMLExecutionProvider', 'CPUExecutionProvider']` when no CUDA device is
present on Darwin arm64. (If a CUDA device is ever visible, CUDA still wins.)

## CPU-only (any machine, default)

No extra packages. `_detect_providers()` returns `['CPUExecutionProvider']`
automatically. This is correct but slow (~4 chunks/s vs 541 on the GPU per
Phase 0); prefer a GPU/CoreML machine for full reindexes.
