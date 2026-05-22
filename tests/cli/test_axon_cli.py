from typer.testing import CliRunner

from axon.__main__ import app

runner = CliRunner()


def test_app_help_shows_axon_branding():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "AXON" in result.stdout


def test_app_with_no_args_shows_help():
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert "Usage" in result.stdout


def test_install_hooks_reports_installed(monkeypatch):
    monkeypatch.setattr(
        "axon.hooks.git_installer.install_hooks",
        lambda repo_path=".": ["post-commit", "post-push"],
    )
    result = runner.invoke(app, ["install-hooks", "--path", "/tmp/repo"])
    assert result.exit_code == 0
    assert "post-commit" in result.stdout
    assert "post-push" in result.stdout


def test_install_hooks_uninstall(monkeypatch):
    monkeypatch.setattr(
        "axon.hooks.git_installer.uninstall_hooks",
        lambda repo_path=".": ["post-commit"],
    )
    result = runner.invoke(app, ["install-hooks", "--uninstall"])
    assert result.exit_code == 0
    assert "removed" in result.stdout
    assert "post-commit" in result.stdout
