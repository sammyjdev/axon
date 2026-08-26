"""Issue #149 regression: interpreter shutdown after the `seed-lessons`
import graph must not write anything to stderr.

The reported crash (`libc++abi: terminating due to uncaught exception of
type std::__1::system_error: recursive_mutex lock failed: Invalid argument`)
happened strictly AFTER `axon seed-lessons` printed its status lines and
returned exit code 0 - a teardown defect, not a functional one. A CliRunner
invocation can never observe this class of defect: it runs in-process and
never finalizes an interpreter. Only a real `subprocess.run` does.

This test does not run the real `axon seed-lessons` command, because that
needs a live Postgres store (`server._get_lesson_store()`), which this suite
must not depend on. Instead it imports the exact modules `seed_lessons` in
`src/axon/cli/pb.py` imports (`axon.mcp.server`, `axon.lessons.seed`) and
drives one asyncio event loop the same way `asyncio.run(_seed())` does in
that command, then lets the interpreter shut down normally. That exercises
interpreter startup and shutdown across the same import graph the bug lives
in (the module-scope onnxruntime/fastembed load in
`axon/embedder/engine.py`, reached transitively) without touching the
network or a database.
"""

from __future__ import annotations

import subprocess
import sys

_TIMEOUT = 60


def test_seed_lessons_import_graph_writes_nothing_to_stderr_at_shutdown() -> None:
    code = (
        "import asyncio\n"
        "import axon.lessons.seed\n"
        "import axon.mcp.server\n"
        "\n"
        "async def _noop():\n"
        "    await asyncio.sleep(0)\n"
        "\n"
        "asyncio.run(_noop())\n"
    )
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=_TIMEOUT,
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr == "", (
        f"interpreter shutdown wrote to stderr (issue #149 regression): {result.stderr!r}"
    )
