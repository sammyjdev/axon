from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

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


DOOR_COMMENT = (
    '# Kept on purpose: volumes created before dec-138 hold the pgvector "vector" extension '
    'and the retired "embeddings" table, which plain postgres:16 cannot load. F1.1 makes '
    "Postgres optional; the image changes with it."
)
IMAGE = "pgvector/pgvector:pg16"
ENVIRONMENT = {"POSTGRES_USER": "axon", "POSTGRES_PASSWORD": "axon", "POSTGRES_DB": "axon"}
VOLUME_TARGET = "/var/lib/postgresql/data"


def test_postgres_service_is_unchanged() -> None:
    text = COMPOSE_FILE.read_text(encoding="utf-8")
    lines = [line.strip() for line in text.splitlines()]
    service = yaml.safe_load(text)["services"]["axon-postgres"]

    assert lines[lines.index(f"image: {IMAGE}") - 1] == DOOR_COMMENT
    assert '- "5433:5432"' in lines
    assert f"- ./data/axon-postgres:{VOLUME_TARGET}" in lines
    assert service == {
        "image": IMAGE,
        "environment": ENVIRONMENT,
        "ports": ["5433:5432"],
        "volumes": [f"./data/axon-postgres:{VOLUME_TARGET}"],
        "restart": "unless-stopped",
    }


@needs_compose
def test_compose_config_keeps_postgres_unchanged() -> None:
    service = json.loads(_compose_config("config", "--format", "json"))["services"]["axon-postgres"]

    [port] = service["ports"]
    assert str(port["published"]) == "5433"
    assert port["target"] == 5432
    [volume] = service["volumes"]
    assert Path(volume["source"]).resolve() == (REPO_ROOT / "data" / "axon-postgres").resolve()
    assert volume["target"] == VOLUME_TARGET
    assert service["image"] == IMAGE
    assert service["environment"] == ENVIRONMENT
    assert service["restart"] == "unless-stopped"
