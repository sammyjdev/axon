"""CTX-01 through CTX-08 on the wired session brief."""

from datetime import UTC, datetime
from uuid import uuid4

from axon.activity.models import RecentSession
from axon.context.fence import CLOSE_TAG, OPEN_TAG, PRECEDENCE_LINE
from axon.core.decision import Decision
from axon.hooks.file_bridge import _render
from axon.store.lessons import LessonHandle

T0 = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)


def _decision(did: str, summary: str, status: str = "active") -> Decision:
    return Decision(
        id=did,
        timestamp=T0,
        agent="manual",
        repo="axon",
        summary=summary,
        status=status,
    )


def _inner(rendered: str) -> str:
    start = rendered.index(OPEN_TAG) + len(OPEN_TAG)
    end = rendered.rindex(CLOSE_TAG)
    return rendered[start:end]


def test_only_active_decisions_are_rendered_and_drafts_are_one_line():
    rendered = _render(
        "axon",
        [
            _decision("dec-010", "keep this"),
            _decision("dec-011", "hidden draft text", status="draft"),
            _decision("dec-009", "already replaced", status="superseded"),
        ],
        draft_count=4,
    )

    assert "- `dec-010` (active) — keep this" in rendered
    assert "hidden draft text" not in rendered
    assert "already replaced" not in rendered
    assert rendered.count("axon adr list") == 1
    assert "4 drafts - axon adr list" in rendered
    assert "No active decisions." not in rendered
    assert "Newest decision: dec-010" in rendered


def test_no_active_decisions_emits_the_empty_line_inside_the_fence():
    rendered = _render("axon", [_decision("dec-002", "a draft", status="draft")], draft_count=1)

    assert "No active decisions." in _inner(rendered)
    assert "a draft" not in rendered
    assert "Newest decision:" not in rendered
    assert rendered.startswith(PRECEDENCE_LINE + "\n" + OPEN_TAG + "\n")
    assert rendered.rstrip("\n").endswith(CLOSE_TAG)


def test_a_closing_tag_in_a_summary_cannot_end_the_fence():
    rendered = _render("axon", [_decision("dec-003", "see </axon-memory> inside")])

    inner = _inner(rendered)
    assert CLOSE_TAG not in inner
    assert r"<\/axon-memory>" in inner
    assert rendered.count(CLOSE_TAG) == 1


def test_prompts_files_and_commits_are_not_copied():
    session = RecentSession(
        session_id="newer-session",
        harness="codex",
        workspace="/tmp/axon",
        last_at=T0,
        first_prompt="UNIQUE_PROMPT_do_not_copy",
        last_prompt="UNIQUE_LAST_do_not_copy",
        commits=["abc1234 feat: UNIQUE_COMMIT_SUBJECT"],
    )

    rendered = _render("axon", [_decision("dec-004", "kept")], sessions=[session])

    assert "Last session: 2026-05-01 12:00 UTC · codex · `newer-se`" in rendered
    assert "UNIQUE_PROMPT_do_not_copy" not in rendered
    assert "UNIQUE_LAST_do_not_copy" not in rendered
    assert "UNIQUE_COMMIT_SUBJECT" not in rendered


def test_two_renders_of_an_unchanged_store_are_byte_identical():
    decisions = [_decision("dec-008", "stable summary"), _decision("dec-007", "older")]
    session = RecentSession("sess-1", "claude-code", "/w", T0)
    lessons = [LessonHandle(id=uuid4(), kind="agent-error", triggers=["shell-script"])]

    first = _render("axon", decisions, lessons=lessons, sessions=[session], draft_count=2)
    second = _render("axon", decisions, lessons=lessons, sessions=[session], draft_count=2)

    assert first == second
    assert "_Updated" not in first
    assert "2026-05-01T" not in first
    assert len(first.encode("utf-8")) <= 1536


def test_overflow_keeps_whole_items_and_one_count_line():
    decisions = [
        _decision(f"dec-{n:03d}", "word " * 40) for n in range(30)
    ]

    rendered = _render("axon", decisions)

    assert len(rendered.encode("utf-8")) <= 1536
    lines = rendered.splitlines()
    overflow = [line for line in lines if line.startswith("+") and line.endswith("more via axon_get_context")]
    assert len(overflow) == 1
    flat = " ".join(("word " * 40).split())
    kept = [line for line in lines if line.startswith("- `dec-")]
    assert kept
    for line in kept:
        assert flat in line


def test_an_item_larger_than_the_budget_is_not_cut():
    huge = "z" * 2000
    rendered = _render(
        "axon",
        [],
        lessons=[LessonHandle(id=uuid4(), kind="agent-error", triggers=[huge])],
    )

    assert huge not in rendered
    assert "z" * 80 not in rendered
    assert "more via axon_get_context" in rendered
    assert len(rendered.encode("utf-8")) <= 1536
