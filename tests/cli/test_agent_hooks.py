"""The Claude Code lifecycle hooks are CLI commands, not loose scripts.

Two scripts previously did this work from ~/.local/bin, outside git, with a
venv path baked into the shebang. Renaming that venv once killed the git hooks
in thirteen repos silently; the same shape was about to repeat here.
"""

import json

from typer.testing import CliRunner

from axon.cli.pb import app

runner = CliRunner()


def _payload(**kw) -> str:
    return json.dumps(kw)


def _transcript(tmp_path, *turns):
    path = tmp_path / "t.jsonl"
    path.write_text(
        "".join(
            json.dumps({"type": r, "message": {"role": r, "content": c}}) + "\n" for r, c in turns
        ),
        encoding="utf-8",
    )
    return path


def test_session_hook_reads_the_transcript_path_from_the_stdin_payload(tmp_path, monkeypatch):
    seen = {}

    def fake_save(*, cwd, transcript):
        seen["cwd"] = cwd
        seen["transcript"] = transcript

    monkeypatch.setattr("axon.cli.pb.session_save", fake_save)
    path = _transcript(tmp_path, ("user", "a"), ("assistant", "b"))

    result = runner.invoke(
        app, ["session-hook"], input=_payload(transcript_path=str(path), cwd=str(tmp_path))
    )

    assert result.exit_code == 0
    assert seen == {"cwd": str(tmp_path), "transcript": str(path)}


def test_session_hook_never_fails_the_harness(tmp_path, monkeypatch):
    """A hook that exits non-zero interrupts the agent. Whatever breaks in
    here, the answer is always exit 0."""

    def boom(**_kw):
        raise RuntimeError("store is down")

    monkeypatch.setattr("axon.cli.pb.session_save", boom)
    path = _transcript(tmp_path, ("user", "a"))

    for payload in (
        _payload(transcript_path=str(path), cwd=str(tmp_path)),
        _payload(cwd=str(tmp_path)),  # no transcript
        "not json at all",
        "",
    ):
        result = runner.invoke(app, ["session-hook"], input=payload)
        assert result.exit_code == 0, payload[:40]


def _compact_transcript(tmp_path, summary):
    path = tmp_path / "c.jsonl"
    path.write_text(
        json.dumps({"type": "assistant", "message": {"role": "assistant", "content": "noise"}})
        + "\n"
        + json.dumps(
            {
                "type": "user",
                "isCompactSummary": True,
                "message": {"role": "user", "content": [{"type": "text", "text": summary}]},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def test_compact_hook_persists_the_last_compact_summary(tmp_path, monkeypatch):
    saved = []

    def fake_store(*, project, summary):
        saved.append((project, summary))

    monkeypatch.setattr("axon.cli.pb._save_compact_summary", fake_store)
    path = _compact_transcript(tmp_path, "the harness already wrote a good summary")

    result = runner.invoke(
        app, ["compact-hook"], input=_payload(transcript_path=str(path), cwd=str(tmp_path))
    )

    assert result.exit_code == 0
    assert saved == [(tmp_path.name, "the harness already wrote a good summary")]


def test_compact_hook_without_a_compact_summary_writes_nothing(tmp_path, monkeypatch):
    saved = []
    monkeypatch.setattr(
        "axon.cli.pb._save_compact_summary",
        lambda **kw: saved.append(kw),
    )
    path = _transcript(tmp_path, ("user", "no compaction happened here"))

    result = runner.invoke(
        app, ["compact-hook"], input=_payload(transcript_path=str(path), cwd=str(tmp_path))
    )

    assert result.exit_code == 0
    assert saved == []


def test_the_last_compact_summary_wins_not_the_first(tmp_path, monkeypatch):
    """A long session compacts more than once; the earlier summary is the one
    the later one already absorbed."""
    saved = []
    monkeypatch.setattr(
        "axon.cli.pb._save_compact_summary", lambda **kw: saved.append(kw["summary"])
    )
    path = tmp_path / "twice.jsonl"
    path.write_text(
        "".join(
            json.dumps(
                {
                    "type": "user",
                    "isCompactSummary": True,
                    "message": {"role": "user", "content": [{"type": "text", "text": text}]},
                }
            )
            + "\n"
            for text in ("the first compaction", "the second compaction")
        ),
        encoding="utf-8",
    )

    runner.invoke(
        app, ["compact-hook"], input=_payload(transcript_path=str(path), cwd=str(tmp_path))
    )

    assert saved == ["the second compaction"]


def test_a_missing_transcript_does_not_reach_the_store(tmp_path, monkeypatch):
    """Exiting 0 is not enough: the guard must stop before the save path, or a
    hook fired without a transcript writes an empty session every time."""
    calls = []
    monkeypatch.setattr("axon.cli.pb.session_save", lambda **kw: calls.append(kw))
    monkeypatch.setattr("axon.cli.pb._save_compact_summary", lambda **kw: calls.append(kw))

    for command in ("session-hook", "compact-hook"):
        result = runner.invoke(
            app, [command], input=_payload(cwd=str(tmp_path), transcript_path="/no/such/file")
        )
        assert result.exit_code == 0

    assert calls == []


def test_every_pb_top_level_command_is_reachable_from_the_installed_binary():
    """`pb.app` is not what `axon` runs - `axon.__main__.app` is, and commands
    have to be re-registered there by hand. That step was forgotten for
    index-vault (PR #150), seed-lessons (PR #145) and again for the hooks, each
    time shipping a command that only existed in the tests. This asserts the
    class, so the next omission fails here instead of in production.
    """
    from typer.main import get_command

    import axon.__main__ as entry

    pb_names = set(get_command(app).commands)
    entry_names = set(get_command(entry.app).commands)

    missing = pb_names - entry_names
    assert not missing, f"registered on pb.app but not on the entry point: {sorted(missing)}"
