"""Claude Code transcript parsing.

The transcript is JSONL - one JSON object per line - and the chat turns live
under a nested `message` object, alongside a majority of lines that are not
chat at all (attachments, mode switches, permission latches). Reading the file
as a single JSON document raises on line 2; reading `role`/`content` from the
top level finds neither. Both mistakes fail to nothing rather than loudly,
which is why this is one function with one test instead of an inline parse.
"""

import json
from pathlib import Path

CHAT_ROLES = ("user", "assistant")


def _text_of(content: object) -> str:
    """A turn's text. Assistant content is a block list; only `text` blocks are
    the turn - thinking and tool_use blocks are not what was said."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return ""


def parse_transcript_turns(path: str | Path) -> list[dict[str, str]]:
    """Chat turns from a Claude Code transcript, oldest first. A malformed line
    is skipped, never fatal: a truncated transcript still yields its turns."""
    turns: list[dict[str, str]] = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            # A line can be valid JSON and still not be an entry: `42`, `null`,
            # `[1,2]`. Catching only JSONDecodeError let those reach .get() and
            # raise AttributeError, which contradicts this function's own
            # promise that a malformed line is skipped and never fatal - and
            # `axon session save` calls it with no guard of its own.
            if not isinstance(entry, dict):
                continue
            message = entry.get("message")
            if not isinstance(message, dict):
                continue
            role = message.get("role")
            if role not in CHAT_ROLES:
                continue
            text = _text_of(message.get("content", "")).strip()
            if text:
                turns.append({"role": role, "content": text})
    return turns


def last_compact_summary(path: str | Path) -> str:
    """The most recent `/compact` summary in a transcript, or "".

    The harness already produced a dense summary during compaction; reusing it
    costs no model call and beats anything we would generate from the turns it
    replaced.
    """
    summary = ""
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            # Same shape as the sibling parser above, same fix.
            if not isinstance(entry, dict):
                continue
            if not entry.get("isCompactSummary"):
                continue
            message = entry.get("message")
            if not isinstance(message, dict):
                continue
            text = _text_of(message.get("content", "")).strip()
            if text:
                summary = text
    return summary
