from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from axon.context import rtk


@pytest.fixture(autouse=True)
def _clear_resolution_cache() -> None:
    rtk.rtk_binary_path.cache_clear()
    yield
    rtk.rtk_binary_path.cache_clear()


def _make_executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def test_explicit_env_bin_wins(monkeypatch, tmp_path) -> None:
    explicit = _make_executable(tmp_path / "custom" / "rtkx")
    monkeypatch.setenv("AXON_RTK_BIN", str(explicit))
    # Even if PATH has a rtk, the explicit env var takes precedence.
    monkeypatch.setattr(rtk.shutil, "which", lambda name: "/usr/bin/rtk")

    assert rtk.rtk_binary_path() == str(explicit)


def test_bootstrap_dir_used_when_no_env(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("AXON_RTK_BIN", raising=False)
    boot = _make_executable(tmp_path / ".axon" / "bin" / "rtkx")
    monkeypatch.setattr(rtk, "_bootstrap_binary", lambda: boot)
    monkeypatch.setattr(rtk.shutil, "which", lambda name: None)

    assert rtk.rtk_binary_path() == str(boot)


def test_path_rtkx_preferred_over_rtk(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("AXON_RTK_BIN", raising=False)
    monkeypatch.setattr(rtk, "_bootstrap_binary", lambda: tmp_path / "missing" / "rtkx")

    def fake_which(name: str) -> str | None:
        return {"rtkx": "/usr/local/bin/rtkx", "rtk": "/usr/local/bin/rtk"}.get(name)

    monkeypatch.setattr(rtk.shutil, "which", fake_which)

    assert rtk.rtk_binary_path() == "/usr/local/bin/rtkx"


def test_falls_back_to_legacy_rtk(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("AXON_RTK_BIN", raising=False)
    monkeypatch.setattr(rtk, "_bootstrap_binary", lambda: tmp_path / "missing" / "rtkx")

    def fake_which(name: str) -> str | None:
        return "/usr/local/bin/rtk" if name == "rtk" else None

    monkeypatch.setattr(rtk.shutil, "which", fake_which)

    assert rtk.rtk_binary_path() == "/usr/local/bin/rtk"


def test_returns_none_when_nothing_found(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("AXON_RTK_BIN", raising=False)
    monkeypatch.setattr(rtk, "_bootstrap_binary", lambda: tmp_path / "missing" / "rtkx")
    monkeypatch.setattr(rtk.shutil, "which", lambda name: None)

    assert rtk.rtk_binary_path() is None
    assert rtk.rtk_installed() is False


def test_bootstrap_binary_targets_axon_home(monkeypatch) -> None:
    expected_name = "rtkx.exe" if os.name == "nt" else "rtkx"
    boot = rtk._bootstrap_binary()
    assert boot.name == expected_name
    assert boot.parent == Path.home() / ".axon" / "bin"
