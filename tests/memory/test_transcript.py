"""Claude Code writes its transcript as JSONL, one object per line, with the
role and content nested under `message`. Parsing it as a single JSON document
(or reading role/content from the top level) yields nothing at all."""

import json

import pytest

from axon.memory.transcript import parse_transcript_turns


def _line(**kw) -> str:
    return json.dumps(kw) + "\n"


@pytest.fixture
def transcript(tmp_path):
    path = tmp_path / "session.jsonl"
    path.write_text(
        # Noise the real file is full of: no `message`, or a non-chat type.
        _line(type="permission-mode", permissionMode="auto")
        + _line(type="attachment", attachment={"path": "x.py"})
        + _line(
            type="user",
            message={"role": "user", "content": "why does the hook never fire?"},
        )
        + _line(
            type="assistant",
            message={
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "not part of the turn"},
                    {"type": "text", "text": "because the parser reads the wrong shape"},
                    {"type": "tool_use", "name": "Bash", "input": {}},
                ],
            },
        )
        + _line(type="mode", mode="auto"),
        encoding="utf-8",
    )
    return path


def test_parses_jsonl_turns_from_the_nested_message(transcript):
    turns = parse_transcript_turns(transcript)

    assert turns == [
        {"role": "user", "content": "why does the hook never fire?"},
        {"role": "assistant", "content": "because the parser reads the wrong shape"},
    ]


def test_a_jsonl_file_is_not_a_single_json_document(transcript):
    """The regression itself: `json.loads` over the whole file raises, and the
    old parser swallowed that exception into an empty turn list."""
    with pytest.raises(json.JSONDecodeError):
        json.loads(transcript.read_text(encoding="utf-8"))

    assert len(parse_transcript_turns(transcript)) == 2


def test_a_truncated_last_line_does_not_lose_the_turns_before_it(tmp_path):
    path = tmp_path / "killed.jsonl"
    path.write_text(
        _line(type="user", message={"role": "user", "content": "first"})
        + _line(type="assistant", message={"role": "assistant", "content": "second"})
        + '{"type": "user", "message": {"role": "us',
        encoding="utf-8",
    )

    assert [t["content"] for t in parse_transcript_turns(path)] == ["first", "second"]


def test_a_non_chat_role_carried_on_a_message_is_not_a_turn(tmp_path):
    """`message` alone does not make a turn: system/tool entries carry one too."""
    path = tmp_path / "roles.jsonl"
    path.write_text(
        _line(type="system", message={"role": "system", "content": "you are..."})
        + _line(type="tool", message={"role": "tool", "content": "exit 0"})
        + _line(type="user", message={"role": "user", "content": "the only turn"}),
        encoding="utf-8",
    )

    assert parse_transcript_turns(path) == [{"role": "user", "content": "the only turn"}]


def test_a_thinking_block_carrying_a_text_key_is_still_not_the_turn(tmp_path):
    """Blocks are selected by type, not by having a `text` key - reasoning and
    tool payloads are not what the assistant said."""
    path = tmp_path / "blocks.jsonl"
    path.write_text(
        _line(
            type="assistant",
            message={
                "role": "assistant",
                "content": [
                    {"type": "thinking", "text": "SECRET REASONING"},
                    {"type": "tool_result", "text": "RAW TOOL OUTPUT"},
                    {"type": "text", "text": "the answer"},
                ],
            },
        ),
        encoding="utf-8",
    )

    assert parse_transcript_turns(path) == [{"role": "assistant", "content": "the answer"}]
