"""Guard startup against decrypting the credential store during imports.

The guard builds its own subprocess environment because the test suite points
the credential store at ``os.devnull``, which would hide an import-time read.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2] / "src"


@pytest.fixture
def guard_env(tmp_path: Path) -> tuple[dict[str, str], Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stand_in = bin_dir / "systemd-creds"
    stand_in.write_text(
        '#!/bin/sh\necho invoked >> "$AXON_STANDIN_MARKER"\nexit 0\n'
    )
    stand_in.chmod(0o755)

    store = tmp_path / "credentials.cred"
    store.write_bytes(b"opaque blob")
    marker = tmp_path / "systemd-creds-invoked"
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "AXON_CREDENTIALS_FILE": str(store),
        "AXON_STANDIN_MARKER": str(marker),
        "PYTHONPATH": str(_SRC),
    }
    return env, marker


def test_axon_help_neither_decrypts_nor_pays_for_the_store(
    guard_env: tuple[dict[str, str], Path],
) -> None:
    env, marker = guard_env
    durations: list[float] = []

    for _ in range(3):
        started = time.monotonic()
        result = subprocess.run(
            [sys.executable, "-m", "axon", "--help"],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        durations.append(time.monotonic() - started)
        assert result.returncode == 0, result.stderr

    marker_contents = marker.read_text() if marker.exists() else "<absent>"
    assert not marker.exists(), (
        f"systemd-creds marker contents: {marker_contents!r}"
    )
    assert min(durations) < 0.2, f"help durations: {durations}"


@pytest.mark.parametrize("store_present", (True, False))
def test_importing_runtime_never_spawns_systemd_creds(
    guard_env: tuple[dict[str, str], Path],
    store_present: bool,
) -> None:
    env, marker = guard_env
    if not store_present:
        Path(env["AXON_CREDENTIALS_FILE"]).unlink()

    result = subprocess.run(
        [sys.executable, "-c", "import axon.config.runtime"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    marker_contents = marker.read_text() if marker.exists() else "<absent>"
    assert not marker.exists(), (
        f"systemd-creds marker contents: {marker_contents!r}"
    )
