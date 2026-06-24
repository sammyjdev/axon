"""Guard against drift between the agent-scope onboarded-repos list and the repos
actually onboarded on this machine.

Two intentionally separate lists govern "which repos AXON knows about" (see the
"Onboarding layers" note in CLAUDE.md):

- Agent scope: ``~/.claude/axon/ROUTER.md`` (canonical, synced dotfiles).
- Index manifest: ``config/projects.json`` (per-machine, multi-machine by design).

This guard enforces a single, machine-safe invariant on the agent-scope list:

    Every repo onboarded on THIS machine (AXON git hooks installed) MUST appear in
    the canonical ROUTER.md list.

The reverse is allowed: a canonical entry with no local repo just means that repo
lives on another machine. Run as a script it is guarded - if ROUTER.md is absent
(CI / another machine) it skips cleanly, matching the opt-in recall gate pattern.
"""
from __future__ import annotations

from pathlib import Path

_MARKER = "Onboarded repos"


def _hook_signature() -> str:
    """The AXON post-commit hook marker. Reuse the installer's constant so the
    scanner can never drift from what `axon install-hooks` actually writes."""
    try:
        from axon.hooks.git_installer import _BEGIN

        return _BEGIN
    except Exception:
        return "# >>> AXON git hook >>>"


def scan_onboarded_repos(dev_root: Path) -> set[str]:
    """Names of immediate child repos under ``dev_root`` that have AXON git hooks
    installed (the operational truth for "onboarded on this machine")."""
    dev_root = Path(dev_root)
    if not dev_root.is_dir():
        return set()
    sig = _hook_signature()
    found: set[str] = set()
    for child in dev_root.iterdir():
        if not child.is_dir():
            continue
        hook = child / ".git" / "hooks" / "post-commit"
        try:
            if hook.is_file() and sig in hook.read_text(encoding="utf-8", errors="ignore"):
                found.add(child.name)
        except OSError:
            continue
    return found


def parse_canonical_repos(router_md_text: str) -> set[str]:
    """Extract the canonical repo names from ROUTER.md.

    Contract: the names are the first non-empty line after the line containing
    ``Onboarded repos``, comma-separated. Returns an empty set if absent.
    """
    lines = router_md_text.splitlines()
    for i, line in enumerate(lines):
        if _MARKER in line:
            for nxt in lines[i + 1:]:
                if nxt.strip():
                    return {
                        tok.strip().rstrip(".")
                        for tok in nxt.split(",")
                        if tok.strip().rstrip(".")
                    }
            break
    return set()


def find_drift(canonical: set[str], onboarded: set[str]) -> list[str]:
    """Return locally-onboarded repos missing from the canonical list, sorted.

    One-directional: ``onboarded - canonical``. Canonical-only entries are allowed.
    """
    return sorted(onboarded - canonical)


def _default_router_md() -> Path:
    return Path.home() / ".claude" / "axon" / "ROUTER.md"


def _default_dev_root() -> Path:
    return Path.home() / "dev"


def main(argv: list[str] | None = None) -> int:
    """Guarded entrypoint. Returns 0 (ok/skip) or 1 (drift detected).

    Env overrides: ``AXON_ROUTER_MD`` (canonical list file) and ``AXON_DEV_ROOT``
    (where repos live). If ROUTER.md is absent (CI / another machine) it skips
    clean - same opt-in spirit as the recall gate.
    """
    import os

    router_path = Path(os.environ.get("AXON_ROUTER_MD") or _default_router_md())
    dev_root = Path(os.environ.get("AXON_DEV_ROOT") or _default_dev_root())

    if not router_path.is_file():
        print(f"[onboarding-drift] SKIP: ROUTER.md not found at {router_path}")
        return 0

    canonical = parse_canonical_repos(router_path.read_text(encoding="utf-8"))
    onboarded = scan_onboarded_repos(dev_root)
    drift = find_drift(canonical, onboarded)

    if drift:
        print(
            "[onboarding-drift] DRIFT: repos onboarded locally but missing from the "
            f"canonical list in {router_path}:"
        )
        for repo in drift:
            print(f"  - {repo}")
        print("Fix: add them to the 'Onboarded repos' line in ROUTER.md.")
        return 1

    print(
        f"[onboarding-drift] OK: {len(onboarded)} onboarded repo(s) all present in "
        "the canonical list."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
