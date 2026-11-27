from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"


def _compose_runs() -> bool:
    docker = shutil.which("docker")
    if docker is None:
        return False
    try:
        result = subprocess.run(
            [docker, "compose", "version"], capture_output=True, check=False, timeout=30
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _compose_config(*args: str) -> str:
    """Resolve the committed compose file only: `-f` keeps a local override out."""
    result = subprocess.run(
        [shutil.which("docker") or "docker", "compose", "-f", "docker-compose.yml", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


# Skips only outside CI: with `CI` set a missing Compose is a failure.
needs_compose = pytest.mark.skipif(
    not os.environ.get("CI") and not _compose_runs(), reason="docker compose v2 not available"
)


def test_only_postgres_is_defined() -> None:
    assert COMPOSE_FILE.read_text(encoding="utf-8").lower().count("ollama") == 0


@needs_compose
def test_compose_config_lists_only_postgres() -> None:
    services = _compose_config("--profile", "cpu", "--profile", "gpu", "config", "--services")

    assert services.split() == ["axon-postgres"]
