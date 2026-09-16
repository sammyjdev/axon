import json
import subprocess
from pathlib import Path

from axon.activity.adapters.codex import parse_codex_session


def _init_git_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(  # noqa: S603
        ["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True  # noqa: S607
    )
    return path


def test_session_project_resolves_from_a_real_git_repo_cwd(tmp_path: Path):
    repo = _init_git_repo(tmp_path / "codex-repo")
    line = json.dumps(
        {
            "timestamp": "2026-09-13T10:00:00Z",
            "type": "session_meta",
            "payload": {
                "session_id": "s-git",
                "cwd": str(repo),
                "originator": "codex_exec",
            },
        }
    )
    test_file = tmp_path / "rollout.jsonl"
    test_file.write_text(line + "\n")

    res = parse_codex_session(test_file)
    assert res.sessions[0].project == "codex-repo"


def test_session_project_stays_none_outside_a_repo(tmp_path: Path):
    line = json.dumps(
        {
            "timestamp": "2026-09-13T10:00:00Z",
            "type": "session_meta",
            "payload": {
                "session_id": "s-norepo",
                "cwd": "/nonexistent/not-a-repo/anywhere",
                "originator": "codex_exec",
            },
        }
    )
    test_file = tmp_path / "rollout.jsonl"
    test_file.write_text(line + "\n")

    res = parse_codex_session(test_file)
    assert res.sessions[0].project is None


def test_codex_exec_prompt_and_json_events_reconcile_by_native_id():
    path = Path("tests/fixtures/activity/codex/simple_session.jsonl")
    result = parse_codex_session(path)
    
    assert len(result.sessions) == 1
    
    tool_calls = [e for e in result.events if e.kind == "tool_call"]
    tool_results = [e for e in result.events if e.kind == "tool_result"]
    
    assert len(tool_calls) == 1
    assert len(tool_results) == 1
    
    assert tool_calls[0].call_id == tool_results[0].call_id
    assert tool_calls[0].call_id == "call_abc"


def test_codex_exec_without_observable_prompt_is_marked_partial():
    path = Path("tests/fixtures/activity/codex/no_observable_prompt.jsonl")
    
    # 1. Without submitted_prompt
    result_no_prompt = parse_codex_session(path, submitted_prompt=None)
    assert len(result_no_prompt.sessions) == 1
    assert result_no_prompt.sessions[0].coverage == "partial-observable"
    assert any("prompt is unobserved" in w for w in result_no_prompt.warnings)
    assert not any(e.kind == "prompt" for e in result_no_prompt.events)
    
    # 2. With submitted_prompt
    result_with_prompt = parse_codex_session(path, submitted_prompt="do the thing")
    assert len(result_with_prompt.sessions) == 1
    prompts = [e for e in result_with_prompt.events if e.kind == "prompt"]
    assert len(prompts) == 1
    assert prompts[0].content["text"] == "do the thing"
    assert prompts[0].coverage == "partial-observable"


def test_repeated_cumulative_usage_snapshot_does_not_inflate_total():
    path = Path("tests/fixtures/activity/codex/cumulative_usage.jsonl")
    result = parse_codex_session(path)
    
    usages = [e for e in result.events if e.kind == "usage"]
    assert len(usages) == 2
    
    # Assert raw observations
    assert usages[0].content["thread_token_usage"]["total_tokens"] == 100
    assert usages[1].content["thread_token_usage"]["total_tokens"] == 250
    # No derived/aggregated event fabricated


def test_unknown_rollout_format_is_visible():
    path = Path("tests/fixtures/activity/codex/unknown_format.jsonl")
    result = parse_codex_session(path)
    
    assert len(result.sessions) == 1
    assert result.sessions[0].coverage in ("unsupported-format", "partial-observable")
    assert len(result.warnings) > 0


def test_concurrent_same_repo_codex_sessions_remain_separate():
    path_a = Path("tests/fixtures/activity/codex/session_a.jsonl")
    path_b = Path("tests/fixtures/activity/codex/session_b.jsonl")
    
    res_a = parse_codex_session(path_a)
    res_b = parse_codex_session(path_b)
    
    assert res_a.sessions[0].session_id == "session_A"
    assert res_b.sessions[0].session_id == "session_B"
    
    assert res_a.sessions[0].session_id != res_b.sessions[0].session_id
    
    assert all(e.session_id == "session_A" for e in res_a.events)
    assert all(e.session_id == "session_B" for e in res_b.events)
