"""Detect copy drift between paired files."""

from pathlib import Path
from typing import Dict, Any
from difflib import SequenceMatcher


class DriftCheck:
    """Validate file pairs don't drift (copies should stay in sync)."""

    def __init__(self, projects_root: Path = Path.home() / "dev"):
        self.projects_root = projects_root
        self.axon_path = projects_root / "axon"
        self.forge_path = Path.home() / "code" / "forge"

    def check_forge_vs_axon_router(self) -> Dict[str, Any]:
        """Compare FORGE oneshot_backend.py with AXON llm_backend.py."""
        forge_file = self.forge_path / "scripts" / "oneshot_backend.py"
        axon_file = self.axon_path / "axon" / "router" / "llm_backend.py"

        if not forge_file.exists() or not axon_file.exists():
            return {
                "status": "missing",
                "forge_exists": forge_file.exists(),
                "axon_exists": axon_file.exists(),
                "severity": "warning",
            }

        try:
            forge_content = forge_file.read_text()
            axon_content = axon_file.read_text()

            # Compute similarity
            matcher = SequenceMatcher(None, forge_content, axon_content)
            similarity = matcher.ratio()

            # If similarity < 0.70 (30% diverged), it's concerning
            diff_percent = int((1 - similarity) * 100)

            status = "warning" if diff_percent > 20 else "ok"

            return {
                "status": status,
                "similarity_ratio": round(similarity, 3),
                "diff_percent": diff_percent,
                "severity": "warning",
            }
        except Exception as e:
            return {
                "status": "error",
                "severity": "warning",
                "error": str(e),
            }

    def run(self) -> Dict[str, Dict[str, Any]]:
        """Run all drift checks."""
        return {
            "forge_vs_axon_router": self.check_forge_vs_axon_router(),
        }
