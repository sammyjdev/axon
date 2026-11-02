"""Check dependency pins (floating vs semver)."""

from pathlib import Path
from typing import Any


class VersioningCheck:
    """Validate dependency versioning across projects.

    GLYPH pin and eval-dependency checks were removed with dec-138: AXON no
    longer vendors the code graph, so those keys are not part of this report
    (ECO-01).
    """

    def __init__(self, projects_root: Path = Path.home() / "dev"):
        self.projects_root = projects_root

    def check_forge_axon_dep(self) -> dict[str, Any]:
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

    def run(self) -> dict[str, dict[str, Any]]:
        """Run all versioning checks."""
        return {
            "forge_axon_dep": self.check_forge_axon_dep(),
        }
