"""Check CI status for all projects."""

import subprocess
from pathlib import Path
from typing import Dict, Any


class CIStatusCheck:
    """Check CI status via git (placeholder for GitHub API)."""

    def __init__(self, projects_root: Path = Path.home() / "dev"):
        self.projects_root = projects_root
        self.projects = {
            "axon": projects_root / "axon",
            "glyph": projects_root / "glyph-kg",
            "gnomon": projects_root / "gnomon-eval",
            "forge": Path.home() / "code" / "forge",
        }

    def get_last_commit_hash(self, repo_path: Path) -> str:
        """Get last commit hash via git."""
        if not repo_path.exists():
            return "N/A"

        try:
            result = subprocess.run(
                ["git", "-C", str(repo_path), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return result.stdout.strip()[:7] if result.returncode == 0 else "N/A"
        except Exception:
            return "N/A"

    def get_last_commit_message(self, repo_path: Path) -> str:
        """Get last commit message."""
        if not repo_path.exists():
            return "N/A"

        try:
            result = subprocess.run(
                ["git", "-C", str(repo_path), "log", "-1", "--pretty=%s"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return (
                result.stdout.strip() if result.returncode == 0 else "N/A"
            )
        except Exception:
            return "N/A"

    def get_ci_status(self, project: str) -> Dict[str, Any]:
        """Get CI status (via git, not GitHub API)."""
        repo_path = self.projects.get(project)
        if not repo_path:
            return {"status": "unknown", "error": f"Project {project} not found"}

        last_commit = self.get_last_commit_hash(repo_path)
        last_message = self.get_last_commit_message(repo_path)

        return {
            "status": "unknown",
            "note": "GitHub API integration pending",
            "last_commit": last_commit,
            "last_message": last_message,
            "severity": "info",
        }

    def run(self) -> Dict[str, Dict[str, Any]]:
        """Run all CI checks."""
        return {
            "axon": self.get_ci_status("axon"),
            "glyph": self.get_ci_status("glyph"),
            "gnomon": self.get_ci_status("gnomon"),
            "forge": self.get_ci_status("forge"),
        }
