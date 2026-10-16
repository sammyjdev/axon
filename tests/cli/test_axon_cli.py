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
    assert "capture checks" in result.stdout
    assert "## Presence" in result.stdout
    assert "## Liveness" in result.stdout
    assert "axon: ok" in result.stdout
    assert "caveman" not in result.stdout


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


def test_init_installs_hooks_and_indexes_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "axon.hooks.git_installer.install_hooks",
        lambda repo_path=".": ["post-commit"],
    )

    result = runner.invoke(app, ["init", str(tmp_path)])
    assert result.exit_code == 0
    assert "post-commit" in result.stdout
    assert "indexed" not in result.stdout


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
    for name in ("adr", "profile", "session"):
        assert name in names


def test_cut_commands_absent():
    names = _registered_command_names()
    for name in (
        "ask",
        "index",
        "watch",
        "til",
        "deep",
        "expand",
        "career",
        "cost",
        "rtk",
        "rtk-status",
        "rtk-init",
        "rtk-install",
        "rtk-proxy",
        "run",
        "git",
        # retired with code vectorization, 2026-10-15
        "graph",
        "index-compare",
        "index-dev",
        "index-prune-legacy",
        "index-vault",
        "ingest-vault",
        "scan",
        "search",
        "sync",
    ):
        assert name not in names


def test_retired_adr_subcommands_absent():
    from typer.main import get_command

    adr = get_command(app).commands["adr"]
    for name in ("audit", "hook", "infer-commit", "review", "validate-drafts"):
        assert name not in adr.commands


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


def test_setup_and_configure_registered():
    names = _registered_command_names()
    for name in ("setup", "configure"):
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
    # Inspect the parsed command rather than rendered --help text: the help is
    # rendered differently depending on whether rich colouring is active, and CI
    # emits ANSI escapes that broke the old substring assertions while the
    # wiring itself was fine.
    from typer.main import get_command

    command = get_command(app).commands["session-save"]

    # session_save's own options - `note` takes a positional TEXT argument and
    # has neither of these, so this fails under an alias-to-`note` miswiring.
    param_names = {p.name for p in command.params}
    assert {"cwd", "transcript"} <= param_names, param_names
    # Distinctive wording from session_save's docstring; note's docstring
    # ("Alias para pb session note.") shares none of it.
    help_text = command.help or ""
    assert "session memory" in help_text
    # "Stop/SessionEnd" are the real Claude Code hook events; the docstring
    # used to name a "PostStop" hook that does not exist in any harness.
    assert "Stop/SessionEnd" in help_text


def test_hooks_and_pending_subapps_registered():
    names = _registered_command_names()
    for name in ("hooks", "pending"):
        assert name in names


def test_hooks_subapp_is_invocable():
    result = runner.invoke(app, ["hooks", "--help"])
    assert result.exit_code == 0
    assert "install" in result.stdout


def test_pending_subapp_is_invocable():
    result = runner.invoke(app, ["pending", "--help"])
    assert result.exit_code == 0
    assert "drain" in result.stdout
