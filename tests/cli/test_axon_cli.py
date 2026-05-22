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
