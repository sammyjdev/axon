"""Obsidian vault discovery (T5.4).

Resolves the vault path without ever creating one. Resolution order:

1. ``AXON_VAULT`` environment variable
2. runtime config ``vault_root``
3. ``~/Documents/ObsidianVault``
4. ``~/Obsidian``

A candidate counts only if it is a directory containing an ``.obsidian/``
folder. The result is cached for the process.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from axon.config.runtime import load_runtime_config

logger = logging.getLogger(__name__)

_cache: dict[str, Path | None] = {}


def _is_vault(path: Path) -> bool:
    return path.is_dir() and (path / ".obsidian").is_dir()


def _candidates() -> list[Path]:
    out: list[Path] = []
    env = os.environ.get("AXON_VAULT")
    if env:
        out.append(Path(env).expanduser())
    out.append(load_runtime_config().vault_root)
    out.append(Path.home() / "Documents" / "ObsidianVault")
    out.append(Path.home() / "Obsidian")
    return out


def discover_vault(*, interactive: bool = False, use_cache: bool = True) -> Path | None:
    """Return the resolved Obsidian vault, or None if none is found.

    Never creates a vault. With ``interactive=True``, prompts on the terminal
    as a last resort.
    """
    if use_cache and "vault" in _cache:
        return _cache["vault"]

    found: Path | None = None
    for candidate in _candidates():
        if _is_vault(candidate):
            found = candidate
            break

    if found is None and interactive:
        raw = input("Path do Obsidian vault (.obsidian/ presente): ").strip()
        if raw:
            candidate = Path(raw).expanduser()
            if _is_vault(candidate):
                found = candidate
            else:
                logger.warning("not a valid Obsidian vault: %s", candidate)

    if found is None:
        logger.warning("no Obsidian vault discovered")
    _cache["vault"] = found
    return found


def clear_cache() -> None:
    """Drop the cached vault path (mainly for tests / re-resolution)."""
    _cache.clear()
