"""Check dependency pins (floating vs semver)."""

import toml
from pathlib import Path
from typing import Dict, Any


class VersioningCheck:
    """Validate dependency versioning across projects."""

    def __init__(self, projects_root: Path = Path.home() / "dev"):
        self.projects_root = projects_root
        self.glyph_path = projects_root / "glyph-kg"
        self.axon_path = projects_root / "axon"

    def check_glyph_eval_dependency_free(self) -> Dict[str, Any]:
        """GLYPH's eval judge must stay free of gnomon-eval/ragas/langchain.

        a3fe54a replaced gnomon-eval (which pulled in ragas + langchain-openai,
        ~30 transitive packages, one of them broken against current
        langchain-community) with a hand-rolled, dependency-free judge. This
        guards against silently reintroducing that dependency chain, rather
        than checking for a pin on a dependency that no longer exists.
        """
        glyph_pyproject = self.glyph_path / "pyproject.toml"

        if not glyph_pyproject.exists():
            return {
                "status": "missing",
                "value": None,
                "severity": "critical",
                "error": "pyproject.toml not found",
            }

        try:
            data = toml.load(glyph_pyproject)
        except Exception as e:
            return {
                "status": "error",
                "value": None,
                "severity": "critical",
                "error": str(e),
            }

        project = data.get("project", {})
        all_deps = list(project.get("dependencies", []))
        for group_deps in project.get("optional-dependencies", {}).values():
            all_deps.extend(group_deps)

        banned = ("gnomon", "ragas", "langchain")
        offenders = [d for d in all_deps if any(b in d.lower() for b in banned)]

        return {
            "status": "critical" if offenders else "ok",
            "value": offenders if offenders else None,
            "severity": "critical",
        }

    def check_glyph_pin_in_axon(self) -> Dict[str, Any]:
        """AXON must pin GLYPH to semver tag."""
        axon_pyproject = self.axon_path / "pyproject.toml"

        if not axon_pyproject.exists():
            return {
                "status": "missing",
                "value": None,
                "severity": "warning",
                "error": "pyproject.toml not found",
            }

        try:
            data = toml.load(axon_pyproject)
        except Exception as e:
            return {
                "status": "error",
                "value": None,
                "severity": "warning",
                "error": str(e),
            }

        deps = data.get("project", {}).get("dependencies", [])
        glyph_dep = next(
            (d for d in deps if "glyph" in d.lower()), None
        )

        if glyph_dep is None:
            return {
                "status": "missing",
                "value": None,
                "severity": "warning",
            }

        # Check if pinned (has @vX.Y.Z or git tag)
        is_pinned = "@" in glyph_dep and any(c.isdigit() for c in glyph_dep)

        return {
            "status": "ok" if is_pinned else "warning",
            "value": glyph_dep,
            "severity": "warning",
        }

    def check_forge_axon_dep(self) -> Dict[str, Any]:
        """FORGE should declare AXON as explicit dependency."""
        forge_path = Path.home() / "code" / "forge"
        deps_file = forge_path / "DEPENDENCIES.md"

        if not forge_path.exists():
            return {
                "status": "missing",
                "value": None,
                "severity": "warning",
                "error": "FORGE path not found",
            }

        if not deps_file.exists():
            return {
                "status": "missing",
                "value": None,
                "severity": "warning",
            }

        try:
            content = deps_file.read_text()
            has_axon = "axon" in content.lower()

            return {
                "status": "ok" if has_axon else "missing",
                "value": "declared in DEPENDENCIES.md" if has_axon else None,
                "severity": "warning",
            }
        except Exception as e:
            return {
                "status": "error",
                "value": None,
                "severity": "warning",
                "error": str(e),
            }

    def run(self) -> Dict[str, Dict[str, Any]]:
        """Run all versioning checks."""
        return {
            "glyph_eval_dependency_free": self.check_glyph_eval_dependency_free(),
            "glyph_pin_in_axon": self.check_glyph_pin_in_axon(),
            "forge_axon_dep": self.check_forge_axon_dep(),
        }
