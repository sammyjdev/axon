# tests/scripts/test_check_onboarding_drift.py
"""Drift guard between the agent-scope onboarded list (~/.claude/axon/ROUTER.md)
and the repos actually onboarded on this machine (AXON git hooks installed).

Invariant (machine-safe, one-directional): every repo onboarded locally MUST be
in the canonical ROUTER.md list. Canonical entries with no local repo are allowed
(other machines), mirroring config/projects.json being multi-machine by design.
"""
from __future__ import annotations


def test_find_drift_flags_local_repo_missing_from_canonical():
    from scripts.check_onboarding_drift import find_drift

    canonical = {"axon", "rtk", "glyph-kg"}
    onboarded = {"axon", "rtk", "new-repo"}  # new-repo onboarded but not listed

    assert find_drift(canonical, onboarded) == ["new-repo"]


def test_find_drift_allows_canonical_repo_absent_locally():
    from scripts.check_onboarding_drift import find_drift

    # "lume" is in the agent list but not onboarded on THIS machine -> allowed.
    canonical = {"axon", "lume"}
    onboarded = {"axon"}

    assert find_drift(canonical, onboarded) == []


_ROUTER_MD = """# AXON Router (context continuity)

AXON is registered as an MCP server and stores project state, decisions, and a
code index across sessions.

**Onboarded repos (canonical - single source of truth; other docs reference, never copy):**
axon, glyph-kg, rtk, lina, lume, pharos-backend, pharos-frontend, revvo-piloto, Orion-AI

When working in an onboarded repo:
- **At the start of a task:** call `axon_get_context`.
"""


def test_parse_canonical_repos_reads_the_list_after_the_marker():
    from scripts.check_onboarding_drift import parse_canonical_repos

    assert parse_canonical_repos(_ROUTER_MD) == {
        "axon", "glyph-kg", "rtk", "lina", "lume",
        "pharos-backend", "pharos-frontend", "revvo-piloto", "Orion-AI",
    }


def test_parse_canonical_repos_empty_when_marker_absent():
    from scripts.check_onboarding_drift import parse_canonical_repos

    assert parse_canonical_repos("# Some other file\n\nno list here\n") == set()


_HOOK_SIG = "# >>> AXON git hook >>>"


def _make_repo(dev_root, name, *, onboarded: bool):
    hooks = dev_root / name / ".git" / "hooks"
    hooks.mkdir(parents=True)
    body = f"#!/bin/sh\n{_HOOK_SIG}\n...\n" if onboarded else "#!/bin/sh\necho plain\n"
    (hooks / "post-commit").write_text(body, encoding="utf-8")


def test_scan_onboarded_repos_detects_axon_hook_signature(tmp_path):
    from scripts.check_onboarding_drift import scan_onboarded_repos

    _make_repo(tmp_path, "axon", onboarded=True)
    _make_repo(tmp_path, "rtk", onboarded=True)
    _make_repo(tmp_path, "not-onboarded", onboarded=False)  # has hook, no AXON sig
    (tmp_path / "plain-dir").mkdir()  # not a git repo at all

    assert scan_onboarded_repos(tmp_path) == {"axon", "rtk"}


def test_scan_onboarded_repos_empty_for_missing_root(tmp_path):
    from scripts.check_onboarding_drift import scan_onboarded_repos

    assert scan_onboarded_repos(tmp_path / "does-not-exist") == set()


def _write_router(tmp_path, names):
    router = tmp_path / "ROUTER.md"
    router.write_text(
        "**Onboarded repos (canonical):**\n" + ", ".join(names) + "\n",
        encoding="utf-8",
    )
    return router


def test_main_returns_0_when_no_local_drift(tmp_path, monkeypatch):
    from scripts.check_onboarding_drift import main

    dev = tmp_path / "dev"
    _make_repo(dev, "axon", onboarded=True)
    router = _write_router(tmp_path, ["axon", "rtk"])  # rtk listed but absent: allowed
    monkeypatch.setenv("AXON_ROUTER_MD", str(router))
    monkeypatch.setenv("AXON_DEV_ROOT", str(dev))

    assert main() == 0


def test_main_returns_1_when_local_repo_missing_from_canonical(tmp_path, monkeypatch):
    from scripts.check_onboarding_drift import main

    dev = tmp_path / "dev"
    _make_repo(dev, "axon", onboarded=True)
    _make_repo(dev, "new-repo", onboarded=True)  # onboarded but not in ROUTER.md
    router = _write_router(tmp_path, ["axon"])
    monkeypatch.setenv("AXON_ROUTER_MD", str(router))
    monkeypatch.setenv("AXON_DEV_ROOT", str(dev))

    assert main() == 1


def test_main_skips_clean_when_router_absent(tmp_path, monkeypatch):
    from scripts.check_onboarding_drift import main

    monkeypatch.setenv("AXON_ROUTER_MD", str(tmp_path / "nope" / "ROUTER.md"))
    monkeypatch.setenv("AXON_DEV_ROOT", str(tmp_path))

    assert main() == 0  # guarded: no ROUTER.md (CI / other machine) -> skip clean


def test_real_machine_has_no_onboarding_drift(monkeypatch):
    """Enforced on the dev machine via the loop gate; skips clean on CI / any
    machine without the canonical ROUTER.md. Catches `axon init <repo>` that
    forgot to update the agent-scope list."""
    import pytest

    from scripts.check_onboarding_drift import _default_router_md, main

    monkeypatch.delenv("AXON_ROUTER_MD", raising=False)
    monkeypatch.delenv("AXON_DEV_ROOT", raising=False)

    if not _default_router_md().is_file():
        pytest.skip("canonical ROUTER.md not present on this machine")

    assert main() == 0, "see stdout: a locally-onboarded repo is missing from ROUTER.md"
