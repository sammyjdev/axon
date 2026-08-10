from axon.embedder.lesson_text import lesson_text


def test_lesson_text_contains_every_trigger_and_tell():
    text = lesson_text(
        kind="wrong-import",
        triggers=["ImportError", "missing module"],
        mistake="imported a module not declared in requirements",
        tell="traceback ends in ModuleNotFoundError",
        fix="add the dependency to requirements.txt",
    )
    assert "ImportError" in text
    assert "missing module" in text
    assert "traceback ends in ModuleNotFoundError" in text


def test_lesson_text_changes_when_only_fix_differs():
    common = dict(
        kind="wrong-import",
        triggers=["ImportError"],
        mistake="imported a module not declared in requirements",
        tell="traceback ends in ModuleNotFoundError",
    )
    a = lesson_text(fix="add to requirements.txt", **common)
    b = lesson_text(fix="pin the version in poetry", **common)
    assert a != b


def test_lesson_text_is_deterministic_for_same_input():
    kwargs = dict(
        kind="k",
        triggers=["t1", "t2"],
        mistake="m",
        tell="tell-signature",
        fix="f",
    )
    assert lesson_text(**kwargs) == lesson_text(**kwargs)
