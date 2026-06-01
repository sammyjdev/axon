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


def test_iter_supported_files_skips_unconventionally_named_virtualenv(tmp_path: Path) -> None:
    # A virtualenv whose directory is not literally ".venv"/"venv" (e.g. a
    # renamed ".venv_hidden", or "py311env") must still be excluded. Every
    # dependency file lives under a "site-packages" segment, so excluding that
    # segment catches the venv regardless of its top-level directory name.
    project = tmp_path / "project"
    src = project / "src"
    src.mkdir(parents=True)
    real = src / "service.py"
    real.write_text("def run():\n    return True\n", encoding="utf-8")

    dep = project / ".venv_hidden" / "lib" / "python3.11" / "site-packages" / "pydantic"
    dep.mkdir(parents=True)
    (dep / "main.py").write_text("class BaseModel:\n    pass\n", encoding="utf-8")

    files = list(iter_supported_files(project))

    assert files == [real]


def test_iter_supported_files_skips_aws_sam_build_artifact(tmp_path: Path) -> None:
    # `sam build` writes bundled Lambda dependencies under ".aws-sam/". The
    # build/ segment catches most of it, but cache and dependency layers can
    # live directly under ".aws-sam/" without a "build" segment, so the
    # ".aws-sam" directory name itself must be excluded — otherwise hundreds of
    # vendored dependency files leak into the index.
    project = tmp_path / "project"
    src = project / "src"
    src.mkdir(parents=True)
    real = src / "handler.py"
    real.write_text("def lambda_handler(event, context):\n    return event\n", encoding="utf-8")

    dep = project / ".aws-sam" / "deps" / "boto3"
    dep.mkdir(parents=True)
    (dep / "client.py").write_text("class Client:\n    pass\n", encoding="utf-8")

    files = list(iter_supported_files(project))

    assert files == [real]


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
