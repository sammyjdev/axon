#!/usr/bin/env python3
"""PreToolUse(Read) hook: AXON coverage collector, plus a one-shot nudge.

Two jobs, both of which only work from inside the agent's harness:

1. Emit the DENOMINATOR. An MCP server only ever sees the calls it received,
   so it is structurally blind to the reads that happened instead of a
   search_code call. Every code Read under ~/dev is logged here as an
   "opportunity", which is what turns the savings ratio into delivered savings.

2. Nudge once per session. The mcp__axon__* tools are deferred, so the model
   cannot call search_code without a ToolSearch first; the rule in AXON.md
   asks for a tool the model cannot see. This injects that missing step.

Never fails the Read: any error exits 0 silently.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

CODE_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".jsx", ".rs", ".java", ".go", ".rb", ".sh"}
ROOT = Path.home() / "dev"

HINT = (
    "AXON is indexed for this tree and its recall telemetry is the evidence "
    "source for the savings claim. Before reading more source files in full, "
    "load and try semantic search:\n"
    '  ToolSearch("select:mcp__axon__search_code") then call it with your question.\n'
    "It returns the relevant excerpts (~1.2K tokens) instead of whole files. "
    "If it comes back empty or off-target, fall back to Read/grep and move on - "
    "do not retry it more than once."
)


def engine_root() -> Path:
    return Path(os.environ.get("AXON_ENGINE", str(Path.home() / "dev" / "axon"))).expanduser()


def chunks_file() -> Path:
    return engine_root() / "data" / "recall" / "chunks.jsonl"


def state_file(session_id: str) -> Path:
    tmp = Path(os.environ.get("TMPDIR", "/tmp"))
    return tmp / f"axon-coverage-{session_id or 'nosession'}.json"


def searches_since(offset: int) -> tuple[int, int]:
    """Count search_code calls logged after `offset` bytes. Returns (count, new_offset).

    Reading only the tail keeps this O(new data) instead of O(telemetry), which
    matters because this runs on every single Read.
    """
    path = chunks_file()
    try:
        size = path.stat().st_size
    except OSError:
        return 0, offset
    if size <= offset:
        return 0, size  # truncated or untouched
    try:
        with path.open("rb") as fh:
            fh.seek(offset)
            tail = fh.read()
    except OSError:
        return 0, offset
    return sum(1 for line in tail.splitlines() if line.strip()), size


def repo_of(path: Path) -> str:
    try:
        parts = path.relative_to(ROOT).parts
    except ValueError:
        return "?"
    # ~/dev/<repo>/... but also ~/dev/products/<repo>/... and ~/dev/tools/<repo>/...
    if parts and parts[0] in {"products", "tools", "learning"} and len(parts) > 1:
        return f"{parts[0]}/{parts[1]}"
    return parts[0] if parts else "?"


def emit_opportunity(path: Path, tool_input: dict, session: str, searches: int) -> None:
    out = engine_root() / "data" / "recall" / "opportunities.jsonl"
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    record = {
        "ts": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ).isoformat(),
        "session": session,
        "kind": "opportunity",
        "repo": repo_of(path),
        "path": str(path),
        # len//4 is the same rough estimator the engine's telemetry already uses
        "est_tokens_full": size // 4,
        "requested_limit": tool_input.get("limit"),
        "searches_in_session": searches,
    }
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, sort_keys=True) + "\n")
    except OSError:
        pass  # telemetry must never break the tool call


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    tool_input = payload.get("tool_input") or {}
    path_str = tool_input.get("file_path") or ""
    if not path_str:
        return 0

    path = Path(path_str)
    if path.suffix not in CODE_SUFFIXES or ROOT not in path.parents:
        return 0

    session = str(payload.get("session_id", ""))
    marker = state_file(session)
    try:
        state = json.loads(marker.read_text())
    except Exception:
        # first code Read of this session: anchor the telemetry cursor
        state = {"offset": chunks_file().stat().st_size if chunks_file().exists() else 0,
                 "hinted": False}

    searches, new_offset = searches_since(state.get("offset", 0))
    total_searches = state.get("searches", 0) + searches
    emit_opportunity(path, tool_input, session, total_searches)

    hinted = bool(state.get("hinted"))
    state.update({"offset": new_offset, "hinted": True, "searches": total_searches})
    try:
        marker.write_text(json.dumps(state))
    except OSError:
        return 0

    if hinted:
        return 0
    json.dump(
        {"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": HINT}},
        sys.stdout,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
