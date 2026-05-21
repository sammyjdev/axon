"""Task 8 — Markdown parser over the spring-migration fixture."""

from __future__ import annotations

from praxis.parser import parse_spec


def test_parse_fixture(spec_text: str) -> None:
    spec = parse_spec(spec_text)

    assert spec.title == "Spring Boot XML-to-Annotation Migration"
    assert spec.goal.startswith("Migrate the legacy Spring XML")

    assert [s.id for s in spec.subtasks] == ["1", "2", "3", "4", "5"]
    assert spec.subtasks[0].title == "Audit the existing XML configuration"
    assert spec.subtasks[4].title == "Remove XML and verify"

    assert spec.subtasks[0].depends_on == []
    assert spec.subtasks[1].depends_on == ["1"]
    assert spec.subtasks[2].depends_on == ["2"]
    assert spec.subtasks[4].depends_on == ["4"]

    assert "Inventory every bean" in spec.subtasks[0].description
    assert "depends_on" not in spec.subtasks[1].description


def test_parse_unnumbered_headings_get_sequential_ids() -> None:
    text = (
        "# Title\n\n"
        "> Goal: do things\n\n"
        "## Tasks\n\n"
        "### First step\n"
        "Do the first thing.\n\n"
        "### Second step\n"
        "Do the second thing.\n"
    )
    spec = parse_spec(text)

    assert spec.goal == "do things"
    assert [s.id for s in spec.subtasks] == ["1", "2"]
    assert spec.subtasks[0].title == "First step"


def test_parse_spec_roundtrips_to_dict(spec_text: str) -> None:
    spec = parse_spec(spec_text)
    data = spec.to_dict()
    assert data["title"] == spec.title
    assert len(data["subtasks"]) == 5
