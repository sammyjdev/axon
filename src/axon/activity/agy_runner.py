import os
import pty
import re
import select
import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol


@dataclass
class AgyRunResult:
    prompt: str
    output: str  # captured, ANSI-stripped stdout+stderr (PTY combines them)
    exit_code: int
    timed_out: bool
    workspace: Path
    started_at: datetime
    finished_at: datetime


def build_agy_argv(*, model: str, prompt: str) -> list[str]:
    """Return the exact argv list: ["agy", "--model", model,
    "--new-project", "-p", prompt] (or your finalized flag set -- `-p` and
    its prompt MUST be the trailing two elements, `--model` must precede
    them). Document any additional flag you include and why."""
    return ["agy", "--model", model, "--new-project", "-p", prompt]


class PopenFactory(Protocol):
    def __call__(self, args: list[str], **kwargs: Any) -> subprocess.Popen: ...


def run_agy(
    *,
    model: str,
    workspace: Path,
    prompt: str,
    timeout: int = 900,
    _popen_factory: PopenFactory = subprocess.Popen,
) -> AgyRunResult:
    """Launch `agy` under a PTY exactly as described above and return the
    captured result. `timed_out=True` and `exit_code=124` when the timeout
    fires; the process group is killed either way on exit. This function
    performs the REAL subprocess invocation -- tests must inject a fake via
    dependency injection (see below), never mock subprocess internals
    directly in a way that hides the real command construction from a test
    that checks argv order/flags.
    """
    argv = build_agy_argv(model=model, prompt=prompt)
    started_at = datetime.now(UTC)

    master_fd, slave_fd = pty.openpty()

    try:
        proc = _popen_factory(
            argv,
            cwd=workspace,
            stdout=slave_fd,
            stderr=slave_fd,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
    finally:
        os.close(slave_fd)

    output_bytes = bytearray()
    timed_out = False
    time_left = float(timeout)

    try:
        while True:
            start_wait = time.time()
            r, _, _ = select.select([master_fd], [], [], max(0.0, time_left))
            elapsed = time.time() - start_wait
            time_left -= elapsed

            if master_fd in r:
                try:
                    data = os.read(master_fd, 4096)
                    if not data:
                        break
                    output_bytes.extend(data)
                except OSError:
                    break
            else:
                if time_left <= 0:
                    timed_out = True
                    break

            if proc.poll() is not None:
                # Process exited, read remaining output with short timeout
                while True:
                    r2, _, _ = select.select([master_fd], [], [], 0.05)
                    if master_fd in r2:
                        try:
                            data = os.read(master_fd, 4096)
                            if not data:
                                break
                            output_bytes.extend(data)
                        except OSError:
                            break
                    else:
                        break
                break

    finally:
        os.close(master_fd)
        if timed_out:
            try:
                os.killpg(proc.pid, 9)
            except OSError:
                pass
            proc.wait()
            exit_code = 124
        else:
            exit_code = proc.wait()
            try:
                os.killpg(proc.pid, 9)
            except OSError:
                pass

    finished_at = datetime.now(UTC)

    raw_output = output_bytes.decode("utf-8", errors="replace")
    ansi_escape = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
    stripped_output = ansi_escape.sub("", raw_output)

    return AgyRunResult(
        prompt=prompt,
        output=stripped_output,
        exit_code=exit_code,
        timed_out=timed_out,
        workspace=workspace,
        started_at=started_at,
        finished_at=finished_at,
    )
