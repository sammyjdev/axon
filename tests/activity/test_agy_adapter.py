import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock

from axon.activity.adapters.agy import build_agy_session
from axon.activity.agy_runner import AgyRunResult, build_agy_argv, run_agy


def test_agy_timeout_and_successful_exit_have_distinct_outcomes():
    # Timeout case
    res_timeout = AgyRunResult(
        prompt="hello",
        output="running...",
        exit_code=124,
        timed_out=True,
        workspace=Path("/tmp"),  # noqa: S108 -- placeholder value, never touched on disk
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
    )
    adapter_res_timeout = build_agy_session(res_timeout, source_id="src1")
    term_event_timeout = next(e for e in adapter_res_timeout.events if e.kind == "terminal_output")
    assert term_event_timeout.outcome == "timed_out"
    assert "process_succeeded" not in term_event_timeout.outcome
    assert "process_failed" not in term_event_timeout.outcome

    # Success case
    res_success = AgyRunResult(
        prompt="hello",
        output="done",
        exit_code=0,
        timed_out=False,
        workspace=Path("/tmp"),  # noqa: S108 -- placeholder value, never touched on disk
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
    )
    adapter_res_success = build_agy_session(res_success, source_id="src2")
    term_event_success = next(e for e in adapter_res_success.events if e.kind == "terminal_output")
    assert term_event_success.outcome == "process_succeeded"
    assert "timed_out" not in term_event_success.outcome


def test_agy_zero_exit_is_not_verified_task_success():
    res = AgyRunResult(
        prompt="hello",
        output="done",
        exit_code=0,
        timed_out=False,
        workspace=Path("/tmp"),  # noqa: S108 -- placeholder value, never touched on disk
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
    )
    adapter_res = build_agy_session(res, source_id="src1")
    term_event = next(e for e in adapter_res.events if e.kind == "terminal_output")

    assert term_event.outcome == "process_succeeded"

    # Assert no event or session field implies verified task success
    for event in adapter_res.events:
        content_str = json.dumps(event.content).lower()
        if event.outcome:
            assert event.outcome == "process_succeeded"
        assert "verified" not in content_str
        assert "success" not in content_str

    for session in adapter_res.sessions:
        assert session.status != "verified"
        assert "success" not in session.status


def test_agy_coverage_marks_internal_actions_unavailable():
    res = AgyRunResult(
        prompt="hello",
        output="done",
        exit_code=0,
        timed_out=False,
        workspace=Path("/tmp"),  # noqa: S108 -- placeholder value, never touched on disk
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
    )
    adapter_res = build_agy_session(res, source_id="src1")

    session = adapter_res.sessions[0]
    assert session.coverage == "partial-observable"

    term_event = next(e for e in adapter_res.events if e.kind == "terminal_output")
    assert term_event.content.get("internal_actions") == "unavailable"
    assert term_event.content.get("usage") == "unavailable"


def test_workspace_diff_does_not_attribute_concurrent_unrelated_edit():
    res = AgyRunResult(
        prompt="hello",
        output="done",
        exit_code=0,
        timed_out=False,
        workspace=Path("/tmp"),  # noqa: S108 -- placeholder value, never touched on disk
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
    )

    # Without diff
    adapter_res_no_diff = build_agy_session(res, source_id="src1")
    diff_events = [e for e in adapter_res_no_diff.events if e.kind == "workspace_diff"]
    assert len(diff_events) == 0

    # With diff
    adapter_res_diff = build_agy_session(res, source_id="src2", workspace_diff=["a.py"])
    diff_events = [e for e in adapter_res_diff.events if e.kind == "workspace_diff"]
    assert len(diff_events) == 1
    assert diff_events[0].content == {"files": ["a.py"]}


def test_build_agy_argv_places_prompt_and_model_correctly():
    argv = build_agy_argv(model="X", prompt="hello")
    assert argv[-2:] == ["-p", "hello"]
    model_idx = argv.index("--model")
    p_idx = argv.index("-p")
    assert model_idx < p_idx


def test_run_agy_with_fake_popen():
    # Inject a fake popen
    fake_popen = Mock()
    fake_popen.pid = 9999
    fake_popen.poll.return_value = 0
    fake_popen.wait.return_value = 0

    saved_slave = []

    def fake_factory(args, **kwargs):
        import os

        slave_fd = kwargs["stdout"]
        saved_slave.append(os.dup(slave_fd))
        os.write(slave_fd, b"mock output")
        return fake_popen

    try:
        res = run_agy(
            model="fake",
            workspace=Path("/tmp"),  # noqa: S108 -- placeholder value, never touched on disk
            prompt="hello",
            timeout=5,
            _popen_factory=fake_factory,
        )
    finally:
        import os

        for fd in saved_slave:
            os.close(fd)

    assert res.output == "mock output"
    assert res.exit_code == 0
    assert not res.timed_out
