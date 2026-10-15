"""Guard: the AXON database comes back after a reboot.

Without a restart policy the reboot of 2026-10-11 left the database down for three
days, and every search, capture and session hook failed until it was started by hand.
"""
from __future__ import annotations

from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent


def test_the_database_container_comes_back_after_a_reboot():
    compose = yaml.safe_load((REPO / "docker-compose.yml").read_text(encoding="utf-8"))

    assert compose["services"]["axon-postgres"].get("restart") == "unless-stopped"
