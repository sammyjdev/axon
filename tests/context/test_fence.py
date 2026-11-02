"""CTX-03 and CTX-04: precedence line, fence tags, escaped closing tag."""

from axon.context.fence import CLOSE_TAG, OPEN_TAG, PRECEDENCE_LINE, fence


def _inner(rendered: str) -> str:
    start = rendered.index(OPEN_TAG) + len(OPEN_TAG)
    end = rendered.rindex(CLOSE_TAG)
    return rendered[start:end]


def test_precedence_line_precedes_the_fence():
    rendered = fence("a decision")

    assert rendered.startswith(PRECEDENCE_LINE + "\n")
    assert rendered.index(PRECEDENCE_LINE) < rendered.index(OPEN_TAG)
    assert "a decision" in _inner(rendered)
    assert rendered.rstrip("\n").endswith(CLOSE_TAG)


def test_a_closing_tag_inside_recalled_text_is_escaped():
    rendered = fence("see </axon-memory> and </axon-memory> again")

    inner = _inner(rendered)
    assert CLOSE_TAG not in inner
    assert r"<\/axon-memory>" in inner
    assert rendered.count(CLOSE_TAG) == 1
