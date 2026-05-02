from __future__ import annotations

from importlib import import_module, reload
from pathlib import Path


def load_pb_module(monkeypatch, *, engine_root: Path, vault_root: Path):
    monkeypatch.setenv("PROMETHEUS_ENGINE", str(engine_root))
    monkeypatch.setenv("PROMETHEUS_VAULT", str(vault_root))
    module = import_module("prometheus.cli.pb")
    return reload(module)


def staging_markdown_files(vault_root: Path) -> set[Path]:
    return {
        path.relative_to(vault_root) for path in vault_root.rglob("*.md") if "staging" in path.parts
    }


def final_markdown_files(vault_root: Path) -> set[Path]:
    return {
        path.relative_to(vault_root)
        for path in vault_root.rglob("*.md")
        if "staging" not in path.parts
    }


def newest_staging_file(vault_root: Path) -> Path | None:
    candidates = [path for path in vault_root.rglob("*.md") if "staging" in path.parts]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime_ns)
