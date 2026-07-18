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
    for url in (
        "postgresql://axon@localhost:5433/axon",
        "postgresql://axon@127.0.0.1:5433/axon",
        "postgresql://axon@[::1]:5433/axon",
        "postgresql://axon@0.0.0.0:5433/axon",
    ):
        r = _run({"AXON_MIRROR_PG_URL": url})
        assert r.returncode == 1, url
        assert "local" in r.stderr, url


def test_dry_run_prints_pipeline() -> None:
    r = _run({"AXON_MIRROR_PG_URL": "postgresql://u:p@ep.sa-east-1.aws.neon.tech/axon"})
    assert r.returncode == 0
    assert "pg_dump" in r.stdout
    assert "pg_restore" in r.stdout