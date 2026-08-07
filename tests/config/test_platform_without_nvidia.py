"""Platform detection must survive a machine with no nvidia-smi (DEBT-3).

`_get_nvidia_vram` guarded on `returncode != 0`, but a missing binary raises
FileNotFoundError out of subprocess.run before any returncode exists. That
propagates through detect_platform() and out of `axon doctor` - so the command
you run when something is already wrong crashed on every Linux/Windows box
without an NVIDIA GPU. macOS never hit it: detect_platform() takes the Darwin
branch and never calls this.
"""
from __future__ import annotations

import subprocess

import pytest

from axon.config.platform import _get_nvidia_vram, detect_platform


def test_missing_nvidia_smi_reports_zero_vram(monkeypatch) -> None:
    def _boom(*_a, **_kw):
        raise FileNotFoundError(2, "No such file or directory", "nvidia-smi")

    monkeypatch.setattr(subprocess, "run", _boom)

    assert _get_nvidia_vram() == 0


def test_detect_platform_survives_missing_nvidia_smi(monkeypatch) -> None:
    """The path that actually broke `axon doctor`."""
    monkeypatch.setattr("platform.system", lambda: "Linux")

    def _boom(*_a, **_kw):
        raise FileNotFoundError(2, "No such file or directory", "nvidia-smi")

    monkeypatch.setattr(subprocess, "run", _boom)

    config = detect_platform()

    assert config.platform == "pc"
    assert config.embedding_providers == ["CPUExecutionProvider"]
    assert config.ollama_flash is False


def test_unparseable_output_reports_zero_vram(monkeypatch) -> None:
    """A present-but-odd nvidia-smi must not crash either."""
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_a, **_kw: subprocess.CompletedProcess([], 0, stdout="not a number\n", stderr=""),
    )

    assert _get_nvidia_vram() == 0


@pytest.mark.parametrize("vram_mib,expected", [("24576", 24), ("8192", 8)])
def test_reports_vram_when_nvidia_smi_works(monkeypatch, vram_mib: str, expected: int) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_a, **_kw: subprocess.CompletedProcess([], 0, stdout=f"{vram_mib}\n", stderr=""),
    )

    assert _get_nvidia_vram() == expected
