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


def _registry_repos() -> set[Path]:
    """Repo roots recorded by ``axon hooks install`` (#185), if the registry exists.

    Env override ``AXON_ONBOARDED_REGISTRY`` for tests. Resolved lazily and
    defensively: this script must still run on a machine with no axon package.
    """
    import json
    import os

    explicit = os.environ.get("AXON_ONBOARDED_REGISTRY")
    if explicit:
        registry = Path(explicit)
    else:
        try:
            from axon.config.runtime import load_runtime_config

            registry = load_runtime_config().data_root / "onboarded_repos.json"
        except Exception:
            return set()
    try:
        entries = json.loads(registry.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    if not isinstance(entries, list):
        return set()
    return {Path(e) for e in entries if isinstance(e, str)}


def _repo_has_axon_hook(repo: Path, sig: str) -> bool:
    """True when AXON capture is wired in ``repo``, by either supported path.

    `axon hooks install` writes the marked block into `.git/hooks/post-commit`
    for a repo with no hook toolchain, but merges entries into
    `.pre-commit-config.yaml` when the pre-commit framework owns the hooks. Only
    checking the written hook made every pre-commit repo (glyph-kg) read as not
    onboarded.
    """
    # A worktree's `.git` is a FILE pointing at the parent's gitdir, and it carries
    # a checked-out copy of `.pre-commit-config.yaml`. Requiring a real `.git`
    # directory keeps `~/dev/axon-worktrees/*` from reading as separate repos.
    if not (repo / ".git").is_dir():
        return False
    hook = repo / ".git" / "hooks" / "post-commit"
    try:
        if hook.is_file() and sig in hook.read_text(encoding="utf-8", errors="ignore"):
            return True
    except OSError:
        pass
    for name in (".pre-commit-config.yaml", ".pre-commit-config.yml"):
        cfg = repo / name
        try:
            if cfg.is_file() and "axon-post-commit" in cfg.read_text(
                encoding="utf-8", errors="ignore"
            ):
                return True
        except OSError:
            continue
    return False


def scan_onboarded_repos(dev_root: Path) -> set[str]:
    """Names of repos under ``dev_root`` with AXON capture wired (the operational
    truth for "onboarded on this machine").

    Two fixed depths, because `~/dev` holds group dirs (`products/`, `tools/`) as
    well as repos - the same layout #185 taught doctor to walk. Scanning immediate
    children only found 1 of 14 onboarded repos on the dev machine.
    """
    sig = _hook_signature()
    candidates: set[Path] = set()

    dev_root = Path(dev_root)
    if dev_root.is_dir():
        for depth in (1, 2):
            candidates.update(
                repo for repo in dev_root.glob("/".join(["*"] * depth)) if repo.is_dir()
            )

    # The glob can only ever see `~/dev`. `axon hooks install` records every root
    # it touches in the registry #185 added, which is how a repo living elsewhere
    # (`~/.claude`) becomes reachable at all. A missing or corrupt registry is not
    # an error - it just means the glob is the whole story on this machine.
    candidates.update(_registry_repos())

    return {repo.name for repo in candidates if _repo_has_axon_hook(repo, sig)}


def parse_canonical_repos(router_md_text: str) -> set[str]:
    """Extract the canonical repo names from ROUTER.md.

    Contract: the names are every line from the first non-empty line after the
    ``Onboarded repos`` marker up to the next blank line, comma-separated.

    Reading a single line broke twice over: a wrapped header made the parser
    return the header's own continuation (`{'never copy):**'}`), and a list long
    enough to wrap lost everything past the first line. Both failures are silent -
    the guard then compares against a markdown fragment and reports plausible
    drift instead of erroring - so the marker line itself is skipped explicitly
    and the block is read to its end.
    """
    lines = router_md_text.splitlines()
    for i, line in enumerate(lines):
        if _MARKER not in line:
            continue
        block: list[str] = []
        started = False
        for nxt in lines[i + 1:]:
            if not nxt.strip():
                if started:
                    break
                continue
            # The header can wrap; its continuation ends in the closing bold
            # marker and carries no commas worth reading.
            if not started and nxt.rstrip().endswith(":**"):
                continue
            started = True
            block.append(nxt)
        return {
            tok.strip().rstrip(".")
            for tok in ",".join(block).split(",")
            if tok.strip().rstrip(".")
        }
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
