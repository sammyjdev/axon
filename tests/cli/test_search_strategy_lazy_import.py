from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"


def test_cli_strategy_selection_does_not_load_mcp_or_litellm(tmp_path: Path) -> None:
    code = (
        "import sys\n"
        "from axon.cli import pb\n"
        "strategy, task_type, profile, mode = pb._select_retrieval_strategy(\n"
        "    'compare retrieval strategies', 'knowledge'\n"
        ")\n"
        "assert strategy.name == 'balanced', strategy\n"
        "assert task_type == 'CODE_ANALYSIS', task_type\n"
        "assert profile is None, profile\n"
        "assert mode == 'full-local', mode\n"
        "unexpected = {'litellm', 'mcp', 'axon.mcp.server'} & sys.modules.keys()\n"
        "assert not unexpected, unexpected\n"
    )
    env = os.environ.copy()
    env["AXON_CONFIG"] = str(tmp_path / "missing.toml")
    env["AXON_RUNTIME_MODE"] = "full-local"
    env["PYTHONPATH"] = os.pathsep.join(
        (str(_SRC), env.get("PYTHONPATH", ""))
    ).rstrip(os.pathsep)

    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", code],
        capture_output=True,
        env=env,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
