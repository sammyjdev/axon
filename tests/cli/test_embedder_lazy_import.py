"""Issue #149: `axon seed-lessons` crashed at interpreter shutdown with
`libc++abi ... recursive_mutex lock failed` after finishing successfully.

Root cause (measured, not re-litigated here): `axon/embedder/engine.py`
imported onnxruntime and fastembed at MODULE SCOPE, so the C++ runtime
(onnxruntime_pybind11_state.so) was loaded into every axon CLI process even
when the default embedder model (bge-m3) never creates an ONNX session - it
routes through an HTTP provider chain instead. That C++ runtime's exit-time
static-destructor teardown is what crashed interpreter shutdown.

These tests run real subprocesses, not `importlib.reload` or an in-process
call: the discriminator is "does merely importing the module put onnxruntime
in sys.modules", which is observable without ever finalizing an interpreter,
so a plain subprocess check is the simplest sufficient tool here.
"""

from __future__ import annotations

import subprocess
import sys

_TIMEOUT = 60


def test_importing_mcp_server_does_not_load_onnxruntime_or_fastembed() -> None:
    """Criterion 1, the deterministic discriminator: importing
    axon.mcp.server - and therefore any axon CLI command that touches the
    MCP server module, including seed-lessons - must not put onnxruntime or
    fastembed in sys.modules. Fails on unfixed code (both load eagerly at
    axon.embedder.engine module scope); passes once those imports are
    deferred into _ensure_model()/_detect_providers().
    """
    code = (
        "import axon.mcp.server\n"
        "import sys\n"
        "assert 'onnxruntime' not in sys.modules, 'onnxruntime must not load eagerly'\n"
        "assert 'fastembed' not in sys.modules, 'fastembed must not load eagerly'\n"
        "print('ok')\n"
    )
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=_TIMEOUT,
    )
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout, result.stdout


def test_dimension_lookups_work_with_no_cpp_runtime_loaded() -> None:
    """Criterion 3: EmbedderEngine().dimension and default_embedding_dimension()
    must keep working, and must not be the thing that pulls onnxruntime or
    fastembed into the process - both are pure dict lookups.
    """
    code = (
        "import sys\n"
        "from axon.embedder.engine import EmbedderEngine, default_embedding_dimension\n"
        "assert 'onnxruntime' not in sys.modules\n"
        "assert 'fastembed' not in sys.modules\n"
        "dim = default_embedding_dimension()\n"
        "assert dim > 0, dim\n"
        "engine = EmbedderEngine(model_name='BAAI/bge-small-en-v1.5')\n"
        "assert engine.dimension == 384, engine.dimension\n"
        "assert 'onnxruntime' not in sys.modules\n"
        "assert 'fastembed' not in sys.modules\n"
        "print('ok')\n"
    )
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=_TIMEOUT,
    )
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout, result.stdout
