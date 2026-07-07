from typer.testing import CliRunner

from axon.__main__ import app

runner = CliRunner()


def test_app_help_shows_axon_branding():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "AXON" in result.stdout


def test_app_with_no_args_shows_help():
    result = runner.invoke(app, [])
    # click >=8.2 exits no-args-is-help with code 2 (was 0); help still on stdout.
    assert result.exit_code in (0, 2)
    assert "Usage" in result.stdout


def test_doctor_runs_and_reports_presence(monkeypatch, tmp_path):
    monkeypatch.setattr("axon.cli.pb._get_db_path", lambda: tmp_path / "axon.db")
    monkeypatch.setenv("AXON_ENGINE", str(tmp_path))
    monkeypatch.setenv("AXON_DATA_ROOT", str(tmp_path))
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code in (0, 1, 2)
    assert "AXON doctor" in result.stdout
    assert "capture & adr checks" in result.stdout
    assert "## Presence" in result.stdout
    assert "## Liveness" in result.stdout
    assert "axon: ok" in result.stdout
    assert "caveman engine: ok" in result.stdout


def test_doctor_supports_ci_mode(monkeypatch, tmp_path):
    monkeypatch.setattr("axon.cli.pb._get_db_path", lambda: tmp_path / "axon.db")
    monkeypatch.setenv("AXON_ENGINE", str(tmp_path))
    monkeypatch.setenv("AXON_DATA_ROOT", str(tmp_path))
    result = runner.invoke(app, ["doctor", "--ci"])
    assert result.exit_code == 0
    import json

    payload = json.loads(result.stdout)
    assert payload["version"] == "1"


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
    assert "vault not found" in result.output


def test_export_rejects_unknown_doc_type(monkeypatch, tmp_path):
    monkeypatch.setattr("axon.obsidian.discovery.discover_vault", lambda **kw: tmp_path)
    _export_store(monkeypatch, [object()])
    result = runner.invoke(app, ["export", "bogus", "--repo", "AXON"])
    assert result.exit_code == 1
    assert "Unknown doc type" in result.output


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
    assert "Not a git repository" in result.output


def test_init_aborts_on_non_git_path(monkeypatch, tmp_path):
    from axon.exceptions import GitAnchorError

    def boom(repo_path="."):
        raise GitAnchorError("not a git repository", repo=str(repo_path))

    monkeypatch.setattr("axon.hooks.git_installer.install_hooks", boom)
    result = runner.invoke(app, ["init", str(tmp_path)])
    assert result.exit_code == 1
    assert "Not a git repository" in result.output


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


def test_setup_configure_index_dev_registered():
    names = _registered_command_names()
    for name in ("setup", "configure", "index-dev"):
        assert name in names


def test_bootstrap_scaffolds_env_and_config(tmp_path):
    engine_dir = tmp_path / "engine"
    vault_dir = tmp_path / "vault"
    result = runner.invoke(
        app, ["bootstrap", "--engine", str(engine_dir), "--vault", str(vault_dir)]
    )
    assert result.exit_code == 0
    assert (engine_dir / ".env.local").exists()
    assert (engine_dir / "axon.toml").exists()
    assert "Scaffold criado" in result.stdout


def test_note_and_session_save_registered():
    names = _registered_command_names()
    for name in ("note", "session-save"):
        assert name in names


def test_session_save_subcommand_already_shared():
    result = runner.invoke(app, ["session", "save", "--help"])
    assert result.exit_code == 0


def test_session_save_alias_is_bound_to_session_save_not_note():
    """Top-level `session-save` must be pb.session_save, not a lookalike.

    `note` also registers as a zero/near-zero-arg top-level command, so
    checking only that the name "session-save" is registered (as
    test_note_and_session_save_registered does) can't catch the alias being
    wired to the wrong function. This asserts on option surface and help text
    that are unique to session_save's real signature/docstring.
    """
    result = runner.invoke(app, ["session-save", "--help"])
    assert result.exit_code == 0
    # session_save's own options - `note` takes a positional TEXT argument
    # and has neither of these, so this fails under an alias-to-`note` miswiring.
    assert "--cwd" in result.stdout
    assert "--transcript" in result.stdout
    # Distinctive wording from session_save's docstring; note's docstring
    # ("Alias para pb session note.") shares none of it.
    assert "session memory" in result.stdout
    assert "PostStop" in result.stdout


def test_hooks_pending_portability_subapps_registered():
    names = _registered_command_names()
    for name in ("hooks", "pending", "portability"):
        assert name in names


def test_hooks_subapp_is_invocable():
    result = runner.invoke(app, ["hooks", "--help"])
    assert result.exit_code == 0
    assert "install" in result.stdout


def test_pending_subapp_is_invocable():
    result = runner.invoke(app, ["pending", "--help"])
    assert result.exit_code == 0
    assert "drain" in result.stdout


def test_portability_subapp_is_invocable():
    """Closes the same discrimination gap as Task 4's fix round: `hooks` and
    `pending` each get a behavioral assertion above, but a sub-app registered
    under the wrong Typer object (e.g. `hooks` accidentally bound to
    `pending_app`) would still pass name-only checks. Assert on portability's
    own subcommand so all three sub-apps have a behavioral check.
    """
    result = runner.invoke(app, ["portability", "--help"])
    assert result.exit_code == 0
    assert "export" in result.stdout
