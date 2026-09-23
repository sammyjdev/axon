"""Check import-level coupling between projects."""

import ast
from pathlib import Path
from typing import Dict, Any, List


class CouplingCheck:
    """Validate coupling (who imports whom)."""

    def __init__(self, projects_root: Path = Path.home() / "dev"):
        self.projects_root = projects_root
        self.axon_path = projects_root / "axon"
        self.glyph_path = projects_root / "glyph-kg"
        self.gnomon_path = projects_root / "gnomon-eval"
        self.forge_path = Path.home() / "code" / "forge"

    def find_imports_in_directory(
        self, directory: Path, pattern: str
    ) -> List[str]:
        """Find all imports matching pattern in a directory."""
        imports = []

        if not directory.exists():
            return imports

        for py_file in directory.rglob("*.py"):
            if any(
                skip in str(py_file)
                for skip in ["venv", "__pycache__", ".venv", "node_modules"]
            ):
                continue

            try:
                tree = ast.parse(py_file.read_text())
            except Exception:
                continue

            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if pattern.lower() in alias.name.lower():
                            imports.append(
                                f"{py_file.name}:{node.lineno} import {alias.name}"
                            )
                elif isinstance(node, ast.ImportFrom):
                    if (
                        node.module
                        and pattern.lower() in node.module.lower()
                    ):
                        imports.append(
                            f"{py_file.name}:{node.lineno} from {node.module}"
                        )

        return imports

    def check_axon_imports_glyph(self) -> Dict[str, Any]:
        """AXON should import GLYPH (expected tight coupling)."""
        imports = self.find_imports_in_directory(self.axon_path, "glyph")

        status = "ok" if imports else "warning"

        return {
            "status": status,
            "imports_found": len(imports),
            "severity": "info",
        }

    def check_glyph_imports_axon(self) -> Dict[str, Any]:
        """GLYPH should NOT import AXON (should be loose)."""
        imports = self.find_imports_in_directory(self.glyph_path, "axon")

        status = "ok" if not imports else "critical"

        return {
            "status": status,
            "imports_found": len(imports),
            "severity": "critical",
        }

    def check_gnomon_imports_axon(self) -> Dict[str, Any]:
        """GNOMON should NOT import AXON (should be config-only)."""
        imports = self.find_imports_in_directory(self.gnomon_path, "axon")

        status = "ok" if not imports else "warning"

        return {
            "status": status,
            "imports_found": len(imports),
            "severity": "critical",
        }

    def check_forge_imports_axon(self) -> Dict[str, Any]:
        """FORGE should NOT import AXON (should be runtime/MCP)."""
        python_files = list(self.forge_path.glob("**/*.py"))

        if not python_files:
            return {
                "status": "ok",
                "imports_found": 0,
                "note": "FORGE has no Python code (as expected)",
                "severity": "info",
            }

        imports = self.find_imports_in_directory(self.forge_path, "axon")

        status = "ok" if not imports else "critical"

        return {
            "status": status,
            "imports_found": len(imports),
            "severity": "critical",
        }

    def run(self) -> Dict[str, Dict[str, Any]]:
        """Run all coupling checks."""
        return {
            "axon_imports_glyph": self.check_axon_imports_glyph(),
            "glyph_imports_axon": self.check_glyph_imports_axon(),
            "gnomon_imports_axon": self.check_gnomon_imports_axon(),
            "forge_imports_axon": self.check_forge_imports_axon(),
        }
