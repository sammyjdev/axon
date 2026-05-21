from __future__ import annotations

from pathlib import Path

from axon.embedder.pipeline import iter_supported_files


def test_iter_supported_files_skips_dependency_and_build_directories(tmp_path: Path) -> None:
    project = tmp_path / "project"
    src = project / "src"
    src.mkdir(parents=True)
    supported = src / "service.py"
    supported.write_text("def run():\n    return True\n", encoding="utf-8")

    for dirname, filename in [
        ("node_modules", "package.ts"),
        (".git", "hook.py"),
        (".venv", "site.py"),
        ("dist", "bundle.ts"),
        ("target", "Generated.java"),
    ]:
        excluded_dir = project / dirname
        excluded_dir.mkdir(parents=True)
        (excluded_dir / filename).write_text("ignored\n", encoding="utf-8")

    files = list(iter_supported_files(project))

    assert files == [supported]


def test_iter_supported_files_applies_language_filter_after_excludes(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    python_file = project / "service.py"
    ts_file = project / "view.ts"
    markdown_file = project / "notes.md"
    python_file.write_text("def run():\n    return True\n", encoding="utf-8")
    ts_file.write_text("export const run = () => true;\n", encoding="utf-8")
    markdown_file.write_text("# Notes\n", encoding="utf-8")

    files = list(iter_supported_files(project, languages={"typescript"}))

    assert files == [ts_file]
