"""Claude Code writes its transcript as JSONL, one object per line, with the
role and content nested under `message`. Parsing it as a single JSON document
(or reading role/content from the top level) yields nothing at all."""

import json

import pytest

from axon.memory.transcript import last_compact_summary, parse_transcript_turns


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


@pytest.mark.parametrize("line", ["42", "null", "true", '"a string"', "[1, 2]"])
def test_a_json_line_that_is_not_an_object_is_skipped(tmp_path, line: str) -> None:
    """The docstring promises a malformed line is skipped, never fatal.

    It only caught JSONDecodeError, so a line that parses as valid JSON but is
    not an object reached `entry.get("message")` and raised AttributeError. The
    `axon session save` path calls this with no guard, turning "capture the
    session, not nothing" into a traceback. Found by the GPT-family
    cross-review, 2026-08-27.
    """
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(
        f'{line}\n'
        '{"message": {"role": "user", "content": "kept"}}\n',
        encoding="utf-8",
    )

    turns = parse_transcript_turns(transcript)

    assert turns == [{"role": "user", "content": "kept"}], (
        "the bad line must be skipped and the good one still returned"
    )


@pytest.mark.parametrize("line", ["42", "null", "[1, 2]"])
def test_last_compact_summary_also_skips_non_object_lines(tmp_path, line: str) -> None:
    """Same defect, same file: the sibling parser had the identical shape."""
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(
        f'{line}\n'
        '{"isCompactSummary": true, "message": {"role": "user", "content": "sum"}}\n',
        encoding="utf-8",
    )

    assert last_compact_summary(transcript) == "sum"
