from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2] / "src"

_CUSTOM_PROFILE_TOML = (
    "[runtime]\n"
    'active_profile = "offline-box"\n'
    "\n"
    "[profiles.offline-box]\n"
    'description = "custom"\n'
    'mode = "hybrid-local"\n'
    'enabled_features = ["offline-first"]\n'
)

# The CLI path must honour the active profile, the profile's mode and its
# capabilities exactly like the MCP server does, while still never loading
# litellm / mcp / axon.mcp.server. Each case samples values where a CLI path
# that drops one of those inputs would return something different.
_CASES = {
    # capabilities from the profile drive the strategy; profile mode overrides env mode
    "custom_profile_capabilities": (
        _CUSTOM_PROFILE_TOML,
        "full-local",
        ("local", "offline-box", "hybrid-local"),
    ),
    # builtin profile resolved by name, its mode wins over the env mode
    "builtin_privacy_first": (
        '[runtime]\nactive_profile = "privacy-first"\n',
        "full-local",
        ("minimal", "privacy-first", "minimal"),
    ),
    # unknown profile is tolerated: name kept, runtime mode kept, no capabilities
    "unknown_profile": (
        '[runtime]\nactive_profile = "ghost"\n',
        "remote-infra",
        ("balanced", "ghost", "remote-infra"),
    ),
    # no profile: runtime mode alone drives the strategy
    "runtime_mode_only": (
        None,
        "minimal",
        ("minimal", None, "minimal"),
    ),
}


def _run_child(code: str, tmp_path: Path, toml: str | None, mode: str) -> dict:
    config_path = tmp_path / "axon.toml"
    if toml is not None:
        config_path.write_text(toml, encoding="utf-8")
    env = os.environ.copy()
    env["AXON_CONFIG"] = str(config_path)
    env["AXON_RUNTIME_MODE"] = mode
    env["PYTHONPATH"] = os.pathsep.join(
        (str(_SRC), env.get("PYTHONPATH", ""))
    ).rstrip(os.pathsep)
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", code],
        capture_output=True,
        env=env,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("case", sorted(_CASES))
def test_cli_strategy_honours_profile_without_loading_mcp(tmp_path: Path, case: str) -> None:
    toml, mode, expected = _CASES[case]
    code = (
        "import json, sys\n"
        "from axon.cli import pb\n"
        "strategy, task_type, profile, mode = pb._select_retrieval_strategy(\n"
        "    'compare retrieval strategies', 'knowledge'\n"
        ")\n"
        "loaded = sorted({'litellm', 'mcp', 'axon.mcp.server'} & sys.modules.keys())\n"
        "print(json.dumps({'strategy': strategy.name, 'task_type': task_type,\n"
        "                  'profile': profile, 'mode': mode, 'loaded': loaded}))\n"
    )

    out = _run_child(code, tmp_path, toml, mode)

    assert out["loaded"] == []
    assert out["task_type"] == "CODE_ANALYSIS"
    assert (out["strategy"], out["profile"], out["mode"]) == expected


@pytest.mark.parametrize("case", sorted(_CASES))
def test_cli_and_mcp_server_select_the_same_strategy(tmp_path: Path, case: str) -> None:
    toml, mode, _expected = _CASES[case]
    code = (
        "import json\n"
        "from axon.cli import pb\n"
        "cli = pb._select_retrieval_strategy('compare retrieval strategies', 'knowledge')\n"
        "from axon.mcp import server\n"
        "srv = server._select_retrieval_strategy('compare retrieval strategies', 'knowledge')\n"
        "def norm(r):\n"
        "    s = r[0]\n"
        "    return [s.name, repr(s), r[1], r[2], r[3]]\n"
        "print(json.dumps({'cli': norm(cli), 'server': norm(srv)}))\n"
    )

    out = _run_child(code, tmp_path, toml, mode)

    assert out["cli"] == out["server"]
