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


def test_init_installs_hooks_and_indexes(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "axon.hooks.git_installer.install_hooks",
        lambda repo_path=".": ["post-commit"],
    )

    class FakeStore:
        def __init__(self, db_path):
            pass

        async def init(self):
            return None

        async def close(self):
            return None

    async def fake_index_repo(repo_path, *, store):
        return ["sym1", "sym2", "sym3"]

    monkeypatch.setattr("axon.store.session_store.SessionStore", FakeStore)
    monkeypatch.setattr("axon.code.indexer.index_repo", fake_index_repo)

    result = runner.invoke(app, ["init", str(tmp_path)])
    assert result.exit_code == 0
    assert "post-commit" in result.stdout
    assert "3 symbols" in result.stdout


def test_serve_starts_mcp_server(monkeypatch):
    called = {"ran": False}

    def fake_main():
        called["ran"] = True

    monkeypatch.setattr("axon.mcp.server.main", fake_main)
    result = runner.invoke(app, ["serve"])
    assert result.exit_code == 0
    assert called["ran"] is True


def test_health_prints_subsystem_report(monkeypatch):
    async def fake_health():
        return "# AXON health\n- sqlite: ok\n- redis: down (boom)"

    monkeypatch.setattr("axon.mcp.server.axon_health", fake_health)
    result = runner.invoke(app, ["health"])
    assert result.exit_code == 0
    assert "AXON health" in result.stdout
    assert "sqlite: ok" in result.stdout
