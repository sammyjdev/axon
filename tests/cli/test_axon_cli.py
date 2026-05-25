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


def test_status_reports_latest_decision(monkeypatch):
    class FakeDecision:
        id = "dec-200"
        summary = "adopt event-driven capture"

    class FakeStore:
        def __init__(self, db_path):
            pass

        async def init(self):
            return None

        async def close(self):
            return None

        async def find_decisions_by_repo(self, repo, limit=20):
            return [FakeDecision()]

    monkeypatch.setattr("axon.store.session_store.SessionStore", FakeStore)
    result = runner.invoke(app, ["status", "--repo", "AXON"])
    assert result.exit_code == 0
    assert "repo: AXON" in result.stdout
    assert "decisions: 1" in result.stdout
    assert "dec-200" in result.stdout


def test_status_handles_no_decisions(monkeypatch):
    class FakeStore:
        def __init__(self, db_path):
            pass

        async def init(self):
            return None

        async def close(self):
            return None

        async def find_decisions_by_repo(self, repo, limit=20):
            return []

    monkeypatch.setattr("axon.store.session_store.SessionStore", FakeStore)
    result = runner.invoke(app, ["status", "--repo", "empty"])
    assert result.exit_code == 0
    assert "latest: none" in result.stdout


def _export_store(monkeypatch, decisions):
    class FakeStore:
        def __init__(self, db_path):
            pass

        async def init(self):
            return None

        async def close(self):
            return None

        async def find_decisions_by_repo(self, repo, limit=100):
            return decisions

    monkeypatch.setattr("axon.store.session_store.SessionStore", FakeStore)


def test_export_architecture(monkeypatch, tmp_path):
    monkeypatch.setattr("axon.obsidian.discovery.discover_vault", lambda **kw: tmp_path)
    monkeypatch.setattr(
        "axon.obsidian.exporter.export_architecture_doc",
        lambda decisions, *, vault, name="architecture": vault / f"{name}.md",
    )
    _export_store(monkeypatch, [object()])
    result = runner.invoke(app, ["export", "architecture", "--repo", "AXON"])
    assert result.exit_code == 0
    assert "architecture doc" in result.stdout


def test_export_aborts_without_vault(monkeypatch):
    monkeypatch.setattr("axon.obsidian.discovery.discover_vault", lambda **kw: None)
    result = runner.invoke(app, ["export", "adr"])
    assert result.exit_code == 1
    assert "vault not found" in result.stdout


def test_export_rejects_unknown_doc_type(monkeypatch, tmp_path):
    monkeypatch.setattr("axon.obsidian.discovery.discover_vault", lambda **kw: tmp_path)
    _export_store(monkeypatch, [object()])
    result = runner.invoke(app, ["export", "bogus", "--repo", "AXON"])
    assert result.exit_code == 1
    assert "Unknown doc type" in result.stdout


def _registered_command_names():
    from typer.main import get_command

    return set(get_command(app).commands.keys())


def test_survivor_subapps_registered():
    names = _registered_command_names()
    for name in ("adr", "graph", "profile", "session"):
        assert name in names


def test_standalone_commands_registered():
    names = _registered_command_names()
    for name in ("scan", "search", "rtk", "rtk-status", "rtk-init", "rtk-proxy", "run", "git"):
        assert name in names


def test_cut_commands_absent():
    names = _registered_command_names()
    for name in ("ask", "index", "watch", "til", "deep", "expand", "career", "cost"):
        assert name not in names


def test_survivor_subapp_is_invocable():
    result = runner.invoke(app, ["adr", "--help"])
    assert result.exit_code == 0
    assert "list" in result.stdout


def test_install_hooks_aborts_on_non_git_path(monkeypatch):
    from axon.exceptions import GitAnchorError

    def boom(repo_path="."):
        raise GitAnchorError("not a git repository", repo=str(repo_path))

    monkeypatch.setattr("axon.hooks.git_installer.install_hooks", boom)
    result = runner.invoke(app, ["install-hooks", "--path", "/tmp/not-a-repo"])
    assert result.exit_code == 1
    assert "Not a git repository" in result.stdout


def test_init_aborts_on_non_git_path(monkeypatch, tmp_path):
    from axon.exceptions import GitAnchorError

    def boom(repo_path="."):
        raise GitAnchorError("not a git repository", repo=str(repo_path))

    monkeypatch.setattr("axon.hooks.git_installer.install_hooks", boom)
    result = runner.invoke(app, ["init", str(tmp_path)])
    assert result.exit_code == 1
    assert "Not a git repository" in result.stdout


def test_export_adr(monkeypatch, tmp_path):
    monkeypatch.setattr("axon.obsidian.discovery.discover_vault", lambda **kw: tmp_path)
    monkeypatch.setattr(
        "axon.obsidian.exporter.export_adr",
        lambda decision, *, vault: vault / "note.md",
    )
    _export_store(monkeypatch, [object(), object()])
    result = runner.invoke(app, ["export", "adr", "--repo", "AXON"])
    assert result.exit_code == 0
    assert "exported 2 ADR notes" in result.stdout


def test_export_summary(monkeypatch, tmp_path):
    monkeypatch.setattr("axon.obsidian.discovery.discover_vault", lambda **kw: tmp_path)
    monkeypatch.setattr(
        "axon.obsidian.exporter.export_project_summary",
        lambda repo, since, decisions, *, vault: vault / "summary.md",
    )
    _export_store(monkeypatch, [object()])
    result = runner.invoke(app, ["export", "summary", "--repo", "AXON"])
    assert result.exit_code == 0
    assert "exported summary" in result.stdout
