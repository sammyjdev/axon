"""scripts/axon-secrets.sh, driven against a stand-in ``systemd-creds`` so the
store logic is checked without a TPM or a user systemd instance."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "axon-secrets.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None,
    reason="axon-secrets.sh is a bash script; bash is not available on PATH",
)

# Stores the payload as-is; STUB_DECRYPT_FAILS makes the store unreadable.
_STUB = """#!/bin/sh
case "$2" in
  encrypt) cat > "$5" ;;
  decrypt) [ -z "${STUB_DECRYPT_FAILS:-}" ] || exit 1; cat "$4" ;;
esac
"""


@pytest.fixture
def secrets(tmp_path: Path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "systemd-creds"
    stub.write_text(_STUB)
    stub.chmod(0o755)
    store = tmp_path / "store" / "credentials.cred"
    base_env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "AXON_CREDENTIALS_FILE": str(store),
    }

    def run(*args: str, stdin: str = "", **extra_env: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603
            ["bash", str(SCRIPT), *args],  # noqa: S607
            input=stdin,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
            env={**base_env, **extra_env},
        )

    run.store = store  # type: ignore[attr-defined]
    return run


def test_set_list_run_unset_roundtrip(secrets) -> None:
    assert secrets("set", "ALPHA", stdin="one\n").returncode == 0
    assert secrets("set", "BETA", stdin="two=2\n").returncode == 0
    assert secrets("set", "ALPHA", stdin="uno\n").returncode == 0

    assert sorted(secrets("list").stdout.split()) == ["ALPHA", "BETA"]
    shown = secrets("run", "--", "sh", "-c", 'printf "%s|%s" "$ALPHA" "$BETA"')
    assert shown.stdout == "uno|two=2"

    assert secrets("unset", "ALPHA").returncode == 0
    assert secrets("list").stdout.split() == ["BETA"]


def test_rejects_empty_values_and_bad_names(secrets) -> None:
    assert secrets("set", "ALPHA", stdin="one\n").returncode == 0
    before = secrets.store.read_bytes()

    assert secrets("set", "ALPHA", stdin="\n").returncode == 2
    assert secrets("set", "not a name", stdin="x\n").returncode == 2
    assert secrets.store.read_bytes() == before


def test_unreadable_store_is_never_replaced(secrets) -> None:
    assert secrets("set", "ALPHA", stdin="one\n").returncode == 0
    before = secrets.store.read_bytes()

    result = secrets("set", "BETA", stdin="two\n", STUB_DECRYPT_FAILS="1")

    assert result.returncode != 0
    assert secrets.store.read_bytes() == before
