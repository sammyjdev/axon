"""Subprocess tests for the live-write session guard in tests/conftest.py.

The guard must fail only for writes made by THIS pytest process. A concurrent
external writer (the operator's MCP server or the AXON git hooks appending to
data/trace/records.jsonl) must be reported as external without failing the
run.

Each test builds a tiny child pytest suite under tmp_path. The child's
conftest.py loads the REAL repo tests/conftest.py by absolute path and
re-exports only the session guard fixture, so the fixture under test is the
real one and its _OPERATOR_* constants bind to the fake AXON_ENGINE /
AXON_VAULT roots passed through the child's environment. Nothing here ever
touches the operator's real vault or engine data: every path a test writes
lives under tmp_path.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

#: Contract string of the guard's AssertionError; it must never be reworded.
GUARD_CONTRACT_MESSAGE = "the suite wrote into the operator's own files"

REAL_CONFTEST = Path(__file__).resolve().parent / "conftest.py"

_CHILD_CONFTEST_TEMPLATE = '''\
"""Load the real repo guard fixture so the child tests the real conftest."""
import importlib.util
import sys

_SPEC = importlib.util.spec_from_file_location("axon_repo_conftest", {conftest!r})
_repo = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _repo
_SPEC.loader.exec_module(_repo)

# Re-export ONLY the guard: the loaded module also defines container-backed
# autouse fixtures that must not activate in this child suite.
_the_suite_never_writes_to_the_operators_files = (
    _repo._the_suite_never_writes_to_the_operators_files
)
'''


def _write_child_suite(tmp_path: Path, child_test_source: str) -> None:
    (tmp_path / "conftest.py").write_text(
        _CHILD_CONFTEST_TEMPLATE.format(conftest=str(REAL_CONFTEST))
    )
    (tmp_path / "test_child.py").write_text(child_test_source)


def _run_child_pytest(
    tmp_path: Path, engine: Path, vault: Path
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["AXON_ENGINE"] = str(engine)
    env["AXON_VAULT"] = str(vault)
    # Keep operator-level pytest overrides out of the child run.
    env.pop("PYTEST_ADDOPTS", None)
    env.pop("PYTEST_PLUGINS", None)
    return subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-m",
            "pytest",
            "test_child.py",
            "-q",
            "-p",
            "no:cacheprovider",
        ],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_external_process_append_does_not_fail_the_guard(tmp_path: Path) -> None:
    engine = tmp_path / "fake-engine"
    vault = tmp_path / "fake-vault"
    vault.mkdir()
    trace_dir = engine / "data" / "trace"
    trace_dir.mkdir(parents=True)
    records = trace_dir / "records.jsonl"
    # Pre-seed so the teardown delta is GROWTH, not creation: the real
    # records.jsonl already exists when the operator's hooks append to it.
    records.write_text('{"seed": "pre-seeded line"}\n')
    size_before = records.stat().st_size
    external_line = "appended by an external writer process\n"

    child_test = f'''\
import subprocess
import sys

RECORDS = {str(records)!r}
LINE = {external_line!r}


def test_external_writer_appends_mid_run():
    code = "open(" + repr(RECORDS) + ", 'a').write(" + repr(LINE) + ")"
    subprocess.run([sys.executable, "-c", code], check=True)
'''
    _write_child_suite(tmp_path, child_test)
    result = _run_child_pytest(tmp_path, engine=engine, vault=vault)

    # Non-vacuous: the external write really happened and the file grew.
    assert records.stat().st_size > size_before
    assert external_line in records.read_text()

    combined = result.stdout + result.stderr
    assert result.returncode == 0, combined
    assert "external" in combined.lower(), combined
    assert str(records) in combined, combined


def test_in_process_write_into_vault_handoffs_fails_the_guard(tmp_path: Path) -> None:
    engine = tmp_path / "fake-engine"
    engine.mkdir()
    vault = tmp_path / "fake-vault"
    handoffs = vault / "knowledge" / "handoffs"
    handoffs.mkdir(parents=True)
    brief = handoffs / "brief-codex.md"

    child_test = f'''\
from pathlib import Path

BRIEF = {str(brief)!r}


def test_in_process_vault_write():
    Path(BRIEF).write_text("handoff brief written by the suite itself")
'''
    _write_child_suite(tmp_path, child_test)
    result = _run_child_pytest(tmp_path, engine=engine, vault=vault)

    combined = result.stdout + result.stderr
    assert result.returncode != 0, combined
    assert GUARD_CONTRACT_MESSAGE in combined, combined
    assert str(brief) in combined, combined


def test_in_process_delete_of_preseeded_vault_handoff_fails_the_guard(
    tmp_path: Path,
) -> None:
    engine = tmp_path / "fake-engine"
    engine.mkdir()
    vault = tmp_path / "fake-vault"
    handoffs = vault / "knowledge" / "handoffs"
    handoffs.mkdir(parents=True)
    brief = handoffs / "brief-vanished.md"
    brief.write_text("pre-seeded brief the suite will unlink\n")

    child_test = f'''\
from pathlib import Path

BRIEF = {str(brief)!r}


def test_in_process_unlink():
    Path(BRIEF).unlink()
'''
    _write_child_suite(tmp_path, child_test)
    result = _run_child_pytest(tmp_path, engine=engine, vault=vault)

    # Non-vacuous: the child really deleted the pre-seeded file, so a guard
    # that only iterates the AFTER snapshot has something to miss.
    assert not brief.exists()

    combined = result.stdout + result.stderr
    assert result.returncode != 0, combined
    assert GUARD_CONTRACT_MESSAGE in combined, combined
    assert str(brief) in combined, combined


def test_in_process_same_size_overwrite_of_preseeded_handoff_fails_the_guard(
    tmp_path: Path,
) -> None:
    engine = tmp_path / "fake-engine"
    engine.mkdir()
    vault = tmp_path / "fake-vault"
    handoffs = vault / "knowledge" / "handoffs"
    handoffs.mkdir(parents=True)
    brief = handoffs / "brief-flipped.md"
    original = "A" * 64
    replacement = "B" * 64
    brief.write_text(original)
    size_before = brief.stat().st_size

    child_test = f'''\
from pathlib import Path

BRIEF = {str(brief)!r}
REPLACEMENT = {replacement!r}


def test_in_process_same_size_overwrite():
    Path(BRIEF).write_text(REPLACEMENT)
'''
    _write_child_suite(tmp_path, child_test)
    result = _run_child_pytest(tmp_path, engine=engine, vault=vault)

    # Non-vacuous: the content changed while the byte count did not, so a
    # size-only comparison has exactly this case to miss.
    assert brief.read_text() == replacement
    assert brief.stat().st_size == size_before

    combined = result.stdout + result.stderr
    assert result.returncode != 0, combined
    assert GUARD_CONTRACT_MESSAGE in combined, combined
    assert str(brief) in combined, combined


def test_in_process_os_open_append_into_engine_trace_fails_the_guard(
    tmp_path: Path,
) -> None:
    engine = tmp_path / "fake-engine"
    vault = tmp_path / "fake-vault"
    vault.mkdir()
    trace_dir = engine / "data" / "trace"
    trace_dir.mkdir(parents=True)
    records = trace_dir / "records.jsonl"
    records.write_text('{"seed": 1}\n')

    child_test = f'''\
import os

RECORDS = {str(records)!r}


def test_in_process_os_open_append():
    fd = os.open(RECORDS, os.O_WRONLY | os.O_APPEND)
    try:
        os.write(fd, b"os-open appended this line\\n")
    finally:
        os.close(fd)
'''
    _write_child_suite(tmp_path, child_test)
    result = _run_child_pytest(tmp_path, engine=engine, vault=vault)

    combined = result.stdout + result.stderr
    assert result.returncode != 0, combined
    assert GUARD_CONTRACT_MESSAGE in combined, combined
    assert str(records) in combined, combined

