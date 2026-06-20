"""Lockfile-based concurrency guard for axon index operations.

Creates .axon/index.lock containing the current PID. On acquisition,
checks if an existing lock's PID is still alive:
  - Alive  -> raise IndexLockError (another indexer is running)
  - Dead   -> reclaim (previous indexer crashed without cleanup)
  - Invalid content -> reclaim (corrupted lockfile)

PLATFORM NOTE (H7 in spec ledger): os.kill(pid, 0) behavior on Windows
differs from Unix. test_index_lock.py::test_pid_alive_windows_terminated_process_h7
validates that _pid_alive returns False for a dead process on Windows 11.
Do NOT remove that test before confirming stale reclaim works in production.
"""
from __future__ import annotations

import ctypes
import os
import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path


class IndexLockError(Exception):
    """Raised when the index lock is held by a live process."""


def _pid_alive(pid: int) -> bool:
    """Return True if a process with the given PID is still running.

    On Unix/macOS: os.kill(pid, 0) raises ProcessLookupError if dead,
    PermissionError if alive but owned by another user (treated as alive).

    On Windows: os.kill(pid, 0) is unreliable for dead-process detection
    (H7 confirmed FAILED on Windows 11 - the call returns True even after
    process exit because the kernel handle remains briefly). We use
    OpenProcess + GetExitCodeProcess via ctypes instead, which correctly
    returns STILL_ACTIVE (259) only for running processes.
    """
    if sys.platform == "win32":
        return _pid_alive_windows(pid)

    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but is owned by a different user - treat as alive
        return True
    except OSError:
        # Covers other edge cases (e.g., invalid PID range on some platforms)
        return False


def _pid_alive_windows(pid: int) -> bool:
    """Windows-specific liveness check using OpenProcess + GetExitCodeProcess.

    SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION = 0x101000.
    GetExitCodeProcess returns STILL_ACTIVE (259) if the process is running.
    OpenProcess returns 0 for nonexistent PIDs; for recently-exited PIDs it
    returns a valid handle but GetExitCodeProcess returns the exit code (not
    STILL_ACTIVE=259). Both cases correctly yield False.
    """
    SYNCHRONIZE = 0x00100000
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259

    handle = ctypes.windll.kernel32.OpenProcess(
        SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, False, pid
    )
    if handle == 0:
        # OpenProcess failed - PID does not exist or is inaccessible
        return False

    exit_code = ctypes.c_ulong(0)
    got = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
    ctypes.windll.kernel32.CloseHandle(handle)

    if not got:
        # GetExitCodeProcess failed unexpectedly - treat as dead
        return False

    return exit_code.value == STILL_ACTIVE


@asynccontextmanager
async def acquire_index_lock(repo_root: Path) -> AsyncGenerator[None, None]:
    """Async context manager that holds .axon/index.lock for repo_root.

    Usage:
        async with acquire_index_lock(Path("/path/to/repo")):
            await index_path(...)

    Raises IndexLockError if a live process already holds the lock.
    Always removes the lockfile on exit (normal or exception).
    """
    lock_path = repo_root / ".axon" / "index.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    if lock_path.exists():
        try:
            existing_pid = int(lock_path.read_text().strip())
            if _pid_alive(existing_pid):
                raise IndexLockError(
                    f"outro processo (pid={existing_pid}) esta indexando {repo_root}. "
                    f"Se travou, remova: {lock_path}"
                )
            # PID is dead - reclaim the stale lock
            lock_path.unlink(missing_ok=True)
        except ValueError:
            # Non-integer content in lockfile - reclaim
            lock_path.unlink(missing_ok=True)

    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        try:
            os.write(fd, str(os.getpid()).encode())
        finally:
            os.close(fd)
    except FileExistsError:
        raise IndexLockError(
            f"Race condition ao adquirir lock em {lock_path}. Tente novamente."
        )
    except Exception:
        # os.open succeeded but os.write or os.close raised - remove the
        # partially-written lockfile so future acquisitions are not blocked.
        lock_path.unlink(missing_ok=True)
        raise

    try:
        yield
    finally:
        lock_path.unlink(missing_ok=True)
