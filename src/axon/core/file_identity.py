"""Pure file identity and kind classification (dec-129, A2, A4)."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path, PurePosixPath
from typing import Literal

Kind = Literal[
    "code",
    "doc",
    "adr",
    "ard",
    "prd",
    "spec",
    "decision",
    "lesson",
    "divergence",
]

FILE_KINDS: frozenset[str] = frozenset(
    {"code", "doc", "adr", "ard", "prd", "spec"}
)
RESERVED_KINDS: frozenset[str] = frozenset(
    {"decision", "lesson", "divergence"}
)
VAULT_REPO: str = "vault"
RepoRoots = dict[str, Path]


def build_repo_roots(
    onboarded: Iterable[Path | str],
    vault_root: Path | str,
) -> RepoRoots:
    """Build mapping of repo identities to absolute roots on this machine."""
    roots: RepoRoots = {}
    for entry in onboarded:
        p = Path(entry)
        # Use directory basename instead of repo_identity() to avoid git subprocesses.
        # Entries in onboarded_repos.json are already resolved at install time, so no
        # filesystem resolve() is called here.
        roots.setdefault(p.name, p)
    roots[VAULT_REPO] = Path(vault_root)
    return roots


def identity_for_path(
    path: Path | str,
    roots: RepoRoots,
) -> tuple[str, str] | None:
    """Resolve an absolute path to (repo, rel_path) against known repo roots."""
    posix_str = str(path).replace("\\", "/")
    posix_path = PurePosixPath(posix_str)
    if not posix_path.is_absolute():
        return None

    parts = posix_path.parts
    best_repo: str | None = None
    best_root_parts: tuple[str, ...] | None = None

    for repo, root in roots.items():
        root_posix = PurePosixPath(str(root).replace("\\", "/"))
        root_parts = root_posix.parts
        # Requires path length strictly greater than root length so root dir itself is excluded.
        if len(parts) > len(root_parts) and parts[: len(root_parts)] == root_parts:
            if best_root_parts is None or len(root_parts) > len(best_root_parts):
                best_repo = repo
                best_root_parts = root_parts

    if best_repo is None or best_root_parts is None:
        return None

    rel_parts = parts[len(best_root_parts) :]
    rel_path = "/".join(rel_parts)
    return best_repo, rel_path


def absolute_for_identity(
    repo: str,
    rel_path: str,
    roots: RepoRoots,
) -> Path | None:
    """Expand (repo, rel_path) to an absolute Path on this machine without disk access."""
    root = roots.get(repo)
    if root is None:
        return None
    clean_rel = rel_path.lstrip("/\\")
    return root / clean_rel


def kind_for_path(rel_path: str) -> Kind:
    """Derive file kind from relative path alone, first match wins."""
    posix_path = PurePosixPath(str(rel_path).replace("\\", "/"))
    # Every segment and stem are casefolded once to match case-insensitively.
    stem = posix_path.stem.casefold()
    name = posix_path.name.casefold()
    suffix = posix_path.suffix.casefold()
    segments = {seg.casefold() for seg in posix_path.parts}

    # 1. ADR rule: stem adr, segment in {adr, adrs}, or dec-*.md under decisions segment
    if (
        stem == "adr"
        or bool(segments & {"adr", "adrs"})
        or ("decisions" in segments and name.startswith("dec-") and name.endswith(".md"))
    ):
        return "adr"

    # 2. ARD rule: stem ard or ard segment
    if stem == "ard" or "ard" in segments:
        return "ard"

    # 3. PRD rule: stem prd or prd segment
    if stem == "prd" or "prd" in segments:
        return "prd"

    # 4. Spec rule: segment in {specs, .specs, plans}
    if bool(segments & {"specs", ".specs", "plans"}):
        return "spec"

    # 5. Doc rule: documentation file extensions
    if suffix in {".md", ".mdx", ".rst", ".txt"}:
        return "doc"

    # 6. Code rule: default for all other supported chunker paths
    return "code"


def load_repo_roots(runtime: object) -> RepoRoots:
    """Load repo roots mapping from runtime.data_root / "onboarded_repos.json" and vault_root.

    Tolerates absent or corrupt registry file. Never scans parent directory.
    """
    data_root = getattr(runtime, "data_root", None)
    vault_root = getattr(runtime, "vault_root", "")
    entries: list[str] = []
    if data_root is not None:
        registry_file = Path(data_root) / "onboarded_repos.json"
        try:
            if registry_file.is_file():
                raw = json.loads(registry_file.read_text(encoding="utf-8"))
                if isinstance(raw, list):
                    entries = [str(e) for e in raw if isinstance(e, (str, Path))]
        except (OSError, ValueError):
            entries = []
    return build_repo_roots(entries, vault_root)


def display_path(file_path: str, project: str, roots: RepoRoots) -> str:
    """Format a stored file path for display or agent consumption.

    Legacy rows with absolute paths or empty project identities are returned verbatim.
    Relative paths under a known repo root expand to an absolute posix path.
    Relative paths without a known root on this machine are formatted with a warning.
    """
    posix_path = PurePosixPath(str(file_path).replace("\\", "/"))
    if posix_path.is_absolute() or not project:
        return file_path
    abs_path = absolute_for_identity(project, file_path, roots)
    if abs_path is not None:
        return abs_path.as_posix()
    return f"{project}:{file_path} (not on this machine)"
