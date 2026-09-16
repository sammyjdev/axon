import json
import subprocess
from pathlib import Path

from axon.activity.adapters.claude_code import parse_claude_code_session

FIXTURE_DIR = Path("tests/fixtures/activity/claude_code").absolute()


def _init_git_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(  # noqa: S603
        ["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True  # noqa: S607
    )
    return path


def test_session_project_resolves_from_a_real_git_repo_cwd(tmp_path: Path):
    repo = _init_git_repo(tmp_path / "my-repo")
    line = json.dumps(
        {
            "type": "user",
            "uuid": "u1",
            "sessionId": "s-git",
            "timestamp": "2026-09-14T13:36:00.000Z",
            "cwd": str(repo),
            "message": {"role": "user", "content": "hello"},
        }
    )
    test_file = tmp_path / "session.jsonl"
    test_file.write_text(line + "\n")

    res = parse_claude_code_session(test_file)
    sessions = {s.session_id: s for s in res.sessions}
    assert sessions["s-git"].project == "my-repo"


def test_session_project_stays_none_outside_a_repo(tmp_path: Path):
    line = json.dumps(
        {
            "type": "user",
            "uuid": "u1",
            "sessionId": "s-norepo",
            "timestamp": "2026-09-14T13:36:00.000Z",
            "cwd": "/nonexistent/not-a-repo/anywhere",
            "message": {"role": "user", "content": "hello"},
        }
    )
    test_file = tmp_path / "session.jsonl"
    test_file.write_text(line + "\n")

    res = parse_claude_code_session(test_file)
    sessions = {s.session_id: s for s in res.sessions}
    assert sessions["s-norepo"].project is None

def test_claude_subagent_events_keep_explicit_parent_session():
    path = FIXTURE_DIR / "parent_and_subagent.jsonl"
    result = parse_claude_code_session(path)
    
    sessions = {s.session_id: s for s in result.sessions}
    assert len(sessions) == 2
    assert "main-s" in sessions
    assert "sub-s" in sessions
    
    assert sessions["main-s"].parent_session_id is None
    assert sessions["sub-s"].parent_session_id == "main-s"

def test_partial_line_is_ingested_after_completion_once(tmp_path: Path):
    orig_path = FIXTURE_DIR / "truncated_session.jsonl"
    test_file = tmp_path / "session.jsonl"
    test_file.write_bytes(orig_path.read_bytes())
    
    res1 = parse_claude_code_session(test_file)
    assert len(res1.events) == 1
    assert res1.events[0].turn_id == "u1"
    
    # Complete the line
    with open(test_file, "a") as f:
        f.write(
            '", "timestamp": "2026-09-14T13:36:01.000Z", '
            '"message": {"role": "assistant", "content": "hi"}}\n'
        )
        
    res2 = parse_claude_code_session(test_file, prior_cursor=res1.cursor)
    assert len(res2.events) == 1
    assert res2.events[0].turn_id == "a1"
    assert res2.events[0].event_id != res1.events[0].event_id

def test_log_replacement_reconciles_without_duplicates(tmp_path: Path):
    orig_path = FIXTURE_DIR / "simple_session.jsonl"
    test_file = tmp_path / "session.jsonl"
    
    orig_lines = orig_path.read_text().splitlines()
    test_file.write_text("\n".join(orig_lines) + "\n")
    
    res1 = parse_claude_code_session(test_file)
    cursor1 = res1.cursor
    
    # Replace the first line to change the fingerprint
    new_first_line = json.loads(orig_lines[0])
    new_first_line["uuid"] = "u1_new"
    orig_lines[0] = json.dumps(new_first_line)
    
    test_file.write_text("\n".join(orig_lines) + "\n")
    
    res2 = parse_claude_code_session(test_file, prior_cursor=cursor1)
    
    # It should re-read from 0, and all except the first event should have the SAME event_id
    res1_ids = {e.event_id for e in res1.events}
    res2_ids = {e.event_id for e in res2.events}
    
    # event for u1 is changed, others remain
    intersection = res1_ids.intersection(res2_ids)
    assert len(intersection) == len(res1.events) - 1

def test_tool_result_links_only_by_native_call_id():
    path = FIXTURE_DIR / "simple_session.jsonl"
    res = parse_claude_code_session(path)
    
    tool_uses = [e for e in res.events if e.kind == "tool_call"]
    tool_results = [e for e in res.events if e.kind == "tool_result"]
    
    assert len(tool_uses) == 1
    assert len(tool_results) == 1
    assert tool_results[0].call_id == tool_uses[0].call_id
    assert tool_results[0].call_id == "toolu_abc"

def test_unparseable_record_becomes_visible_warning():
    path = FIXTURE_DIR / "malformed_line.jsonl"
    res = parse_claude_code_session(path)
    
    events = res.events
    assert len(events) == 3
    
    bad_event = next((e for e in events if e.turn_id == "u2"), None)
    assert bad_event is not None
    assert bad_event.coverage == "unsupported-format"
    
    assert len(res.warnings) == 1
    assert "unsupported user" in res.warnings[0].lower()
