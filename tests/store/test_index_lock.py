"""Unit and integration tests for IndexLock with PID-based stale reclaim.

H7 (spec ledger): os.kill(pid, 0) behavior on Windows 11 is a hypothesis.
test_pid_alive_returns_false_for_dead_pid validates it on the current platform.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from axon.store.index_lock import IndexLockError, _pid_alive, acquire_index_lock


async def test_lock_acquired_and_released(tmp_path: Path) -> None:
    async with acquire_index_lock(tmp_path):
        lock_path = tmp_path / ".axon" / "index.lock"
        assert lock_path.exists(), "Lockfile must exist during context"
    assert not lock_path.exists(), "Lockfile must be removed after context"


async def test_lock_file_contains_current_pid(tmp_path: Path) -> None:
    async with acquire_index_lock(tmp_path):
        lock_path = tmp_path / ".axon" / "index.lock"
        pid_in_file = int(lock_path.read_text().strip())
        assert pid_in_file == os.getpid()


async def test_second_acquire_with_live_pid_raises(tmp_path: Path) -> None:
    lock_path = tmp_path / ".axon" / "index.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(str(os.getpid()))  # current process is alive
    with pytest.raises(IndexLockError, match="outro processo"):
        async with acquire_index_lock(tmp_path):
            pass


async def test_stale_lock_is_reclaimed(tmp_path: Path) -> None:
    lock_path = tmp_path / ".axon" / "index.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text("99999999")  # almost certainly dead
    reached = False
    async with acquire_index_lock(tmp_path):
        reached = True
    assert reached, "Should have reclaimed stale lock and proceeded"


async def test_lock_released_on_exception(tmp_path: Path) -> None:
    lock_path = tmp_path / ".axon" / "index.lock"
    with pytest.raises(RuntimeError):
        async with acquire_index_lock(tmp_path):
            raise RuntimeError("simulated failure")
    assert not lock_path.exists(), "Lockfile must be cleaned up even on exception"


async def test_invalid_pid_content_reclaimed(tmp_path: Path) -> None:
    lock_path = tmp_path / ".axon" / "index.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text("not-a-pid")
    reached = False
    async with acquire_index_lock(tmp_path):
        reached = True
    assert reached


def test_pid_alive_self() -> None:
    assert _pid_alive(os.getpid()) is True


def test_pid_alive_dead_process() -> None:
    # Start a subprocess and wait for it to exit, then verify _pid_alive returns False
    proc = subprocess.Popen([sys.executable, "-c", "import sys; sys.exit(0)"])
    proc.wait()
    dead_pid = proc.pid
    assert _pid_alive(dead_pid) is False, (
        "H7 validation: os.kill(pid, 0) must return False for a dead process on this platform. "
        "If this fails on Windows 11, stale reclaim via PID is not safe - use TTL fallback only."
    )


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-specific H7 validation")
def test_pid_alive_windows_terminated_process_h7() -> None:
    """Explicit H7 validation on Windows 11.

    Creates a process, waits for it to terminate, then checks _pid_alive.
    This test MUST pass before stale reclaim is declared supported on Windows.
    """
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    result = _pid_alive(proc.pid)
    assert result is False, (
        f"H7 FAILED: _pid_alive({proc.pid}) returned True for a dead process. "
        "Do NOT rely on PID-based stale reclaim on this Windows version. "
        "Update index_lock.py to use TTL-only fallback."
    )
