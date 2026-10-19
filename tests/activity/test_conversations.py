from axon.activity.conversations import snippet


def test_the_snippet_shows_the_hit_even_when_the_query_has_a_line_break() -> None:
    text = "x " * 200 + "the cursor\n   table moved" + " y" * 200
    assert "the cursor table moved" in snippet(text, "cursor\n table")
