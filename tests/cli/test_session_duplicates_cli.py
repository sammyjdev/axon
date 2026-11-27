"""#187: `axon session duplicates` reports and nothing else."""
from __future__ import annotations

from typer.testing import CliRunner

from axon.__main__ import app

runner = CliRunner()


def test_report_through_axon_app(monkeypatch) -> None:
    class _Store:
        def __init__(self, *_args) -> None:
            pass

        async def init(self) -> None:
            return None

        async def duplicate_session_memories(self):
            return [("p", 9, [7, 3]), ("/a repo", 4, [1])]

        async def close(self) -> None:
            return None

    monkeypatch.setattr("axon.store.session_store.SessionStore", _Store)

    result = runner.invoke(app, ["session", "duplicates"])

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == [
        "p  keep 9  extra 7,3",
        "/a repo  keep 4  extra 1",
        "2 duplicate group(s), 3 extra row(s); nothing changed",
    ]


def test_store_down_exits_1(monkeypatch) -> None:
    monkeypatch.setenv(
        "AXON_PG_URL", "postgresql://axon:axon@127.0.0.1:1/axon_tests_have_no_database"
    )

    result = runner.invoke(app, ["session", "duplicates"])

    assert result.exit_code == 1
    assert result.stderr.startswith("cannot reach the session store:")
    assert result.stdout == ""


def test_unknown_option_exits_2() -> None:
    # --apply and --restore do not exist: report only (Q2-A)
    for option in ("--nope", "--apply", "--restore"):
        result = runner.invoke(app, ["session", "duplicates", option])

        assert result.exit_code == 2, option
        assert "No such option" in result.stderr


def test_other_session_subcommands_keep_their_callback_line(monkeypatch) -> None:
    class _Store:
        def __init__(self, *_args) -> None:
            pass

        async def init(self) -> None:
            return None

        async def save_note(self, note) -> int:
            return 1

    monkeypatch.setattr("axon.store.session_store.SessionStore", _Store)

    result = runner.invoke(app, ["session", "note", "hello"])

    assert result.exit_code == 0, result.output
    assert "Nenhuma sessão ativa" in result.stdout
