from __future__ import annotations

import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "axon-mirror.sh"


def _run(env_overrides: dict[str, str]) -> subprocess.CompletedProcess[str]:
    env = {"PATH": os.environ["PATH"]}
    env.update(env_overrides)
    return subprocess.run(
        ["bash", str(SCRIPT), "--dry-run"],
        env=env,
        capture_output=True,
        text=True,
    )


def test_refuses_without_target() -> None:
    r = _run({})
    assert r.returncode == 1
    assert "AXON_MIRROR_PG_URL" in r.stderr


def test_refuses_local_target() -> None:
    r = _run({"AXON_MIRROR_PG_URL": "postgresql://axon@localhost:5433/axon"})
    assert r.returncode == 1
    assert "local" in r.stderr


def test_dry_run_prints_pipeline() -> None:
    r = _run({"AXON_MIRROR_PG_URL": "postgresql://u:p@ep.sa-east-1.aws.neon.tech/axon"})
    assert r.returncode == 0
    assert "pg_dump" in r.stdout
    assert "pg_restore" in r.stdout