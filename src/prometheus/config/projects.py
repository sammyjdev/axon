from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from prometheus.context.registry import VALID_CONTEXTS as REGISTERED_CONTEXTS

VALID_CONTEXTS = set(REGISTERED_CONTEXTS)
VALID_LANGUAGES = {"java", "python", "typescript", "markdown", "text"}


@dataclass(frozen=True)
class ProjectEntry:
    name: str
    path: Path
    ctx: str
    enabled: bool
    languages: tuple[str, ...]


def load_project_manifest(path: Path) -> list[ProjectEntry]:
    if not path.exists():
        raise FileNotFoundError(path)

    data = json.loads(path.read_text(encoding="utf-8"))
    raw_projects = data.get("projects")
    if not isinstance(raw_projects, list):
        raise ValueError("Manifesto deve conter uma lista 'projects'.")

    projects: list[ProjectEntry] = []
    seen_names: set[str] = set()
    for index, raw in enumerate(raw_projects, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"Projeto #{index} deve ser um objeto.")

        name = str(raw.get("name", "")).strip()
        if not name:
            raise ValueError(f"Projeto #{index} sem name.")
        if name in seen_names:
            raise ValueError(f"Projeto duplicado no manifesto: {name}")
        seen_names.add(name)

        ctx = str(raw.get("ctx", "")).strip().lower()
        if ctx not in VALID_CONTEXTS:
            raise ValueError(f"Projeto '{name}' usa ctx inválido: {ctx}")

        raw_path = str(raw.get("path", "")).strip()
        if not raw_path:
            raise ValueError(f"Projeto '{name}' sem path.")
        project_path = Path(raw_path).expanduser()
        if not project_path.exists():
            raise FileNotFoundError(project_path)

        raw_languages = raw.get("languages", [])
        if not isinstance(raw_languages, list) or not raw_languages:
            raise ValueError(f"Projeto '{name}' deve declarar languages.")
        languages = tuple(str(lang).strip().lower() for lang in raw_languages if str(lang).strip())
        invalid = set(languages) - VALID_LANGUAGES
        if invalid:
            raise ValueError(f"Projeto '{name}' usa languages inválidas: {sorted(invalid)}")

        projects.append(
            ProjectEntry(
                name=name,
                path=project_path,
                ctx=ctx,
                enabled=bool(raw.get("enabled", True)),
                languages=languages,
            )
        )

    return projects
