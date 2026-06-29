"""Tests for Obsidian doc export (T5.3)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from axon.core.decision import Decision
from axon.obsidian.exporter import (
    export_adr,
    export_architecture_doc,
    export_project_summary,
)


def _decision(**overrides: Any) -> Decision:
    base: dict[str, Any] = dict(
        id="dec-001",
        timestamp=datetime(2026, 5, 10, tzinfo=UTC),
        agent="claude-code",
        repo="axon",
        summary="drop neo4j backend",
    )
    base.update(overrides)
    return Decision(**base)


def test_export_adr_writes_decision_note(tmp_path: Path) -> None:
    decision = _decision(symbols=["pkg.Mod"], linked_decisions=["dec-002"])
    target = export_adr(decision, vault=tmp_path)

    assert target == tmp_path / "AXON" / "Decisions" / "dec-001.md"
    text = target.read_text(encoding="utf-8")
    assert "drop neo4j backend" in text
    assert "pkg.Mod" in text
    assert "[[dec-002]]" in text


def test_export_architecture_doc_wikilinks_decisions(tmp_path: Path) -> None:
    target = export_architecture_doc(
        [_decision(id="dec-001"), _decision(id="dec-002")], vault=tmp_path
    )
    text = target.read_text(encoding="utf-8")
    assert "[[dec-001]]" in text and "[[dec-002]]" in text


def test_export_project_summary_filters_by_date(tmp_path: Path) -> None:
    old = _decision(id="dec-001", timestamp=datetime(2026, 1, 1, tzinfo=UTC))
    new = _decision(id="dec-002", timestamp=datetime(2026, 5, 20, tzinfo=UTC))
    target = export_project_summary(
        "axon", date(2026, 5, 1), [old, new], vault=tmp_path
    )
    text = target.read_text(encoding="utf-8")
    assert "[[dec-002]]" in text
    assert "[[dec-001]]" not in text


def test_write_is_atomic_and_leaves_no_tmp(tmp_path: Path) -> None:
    export_adr(_decision(), vault=tmp_path)
    decisions_dir = tmp_path / "AXON" / "Decisions"
    assert (decisions_dir / "dec-001.md").exists()
    assert not (decisions_dir / "dec-001.md.tmp").exists()


def test_export_does_not_touch_existing_vault_notes(tmp_path: Path) -> None:
    existing = tmp_path / "my-note.md"
    existing.write_text("untouched", encoding="utf-8")
    export_adr(_decision(), vault=tmp_path)
    assert existing.read_text(encoding="utf-8") == "untouched"


def test_export_adr_uses_frontmatter_and_clean_heading(tmp_path: Path) -> None:
    decision = _decision(
        symbols=["pkg.Mod"], linked_decisions=["dec-002"], tags=["embedder", "chunking"]
    )
    text = export_adr(decision, vault=tmp_path).read_text(encoding="utf-8")
    # frontmatter block with facets, body with clean heading
    assert text.startswith("---\n")
    assert "id: dec-001" in text
    assert "symbols:" in text and "pkg.Mod" in text
    # H1 is the summary, NOT "# dec-001 — ..."
    assert "# drop neo4j backend" in text
    assert "# dec-001" not in text
    # tags as obsidian hashtags + related wikilink in the body
    assert "#embedder" in text and "#chunking" in text
    assert "**Related:** [[dec-002]]" in text


def test_export_adr_chunks_without_redundant_breadcrumb(tmp_path: Path) -> None:
    from axon.embedder.chunker import chunk_source

    path = export_adr(_decision(), vault=tmp_path)
    chunks = chunk_source(path.read_text(encoding="utf-8"), "markdown", str(path))
    symbols = [c.symbol for c in chunks]
    contents = "\n".join(c.content for c in chunks)
    assert "dec-001 > drop neo4j backend" in symbols
    assert "dec-001 > dec-001" not in " ".join(symbols)  # no doubled id
    assert "validation_score" not in contents  # facets not embedded
    assert "---" not in contents


def test_architecture_doc_groups_by_status(tmp_path: Path) -> None:
    decisions = [
        _decision(id="dec-001", status="active", summary="keep postgres"),
        _decision(id="dec-002", status="superseded", summary="old qdrant path"),
    ]
    text = export_architecture_doc(decisions, vault=tmp_path).read_text(encoding="utf-8")
    assert text.startswith("---\n")  # frontmatter
    assert "## Active" in text and "## Superseded" in text
    assert "- [[dec-001]] — keep postgres" in text
    assert "- [[dec-002]] — old qdrant path" in text
    # Active group precedes Superseded group
    assert text.index("## Active") < text.index("## Superseded")


def test_architecture_doc_empty_renders_none(tmp_path: Path) -> None:
    text = export_architecture_doc([], vault=tmp_path).read_text(encoding="utf-8")
    assert "_None._" in text


def test_summary_filters_by_date_and_groups(tmp_path: Path) -> None:
    old = _decision(id="dec-001", timestamp=datetime(2026, 1, 1, tzinfo=UTC))
    new = _decision(id="dec-002", status="active", timestamp=datetime(2026, 5, 20, tzinfo=UTC))
    text = export_project_summary(
        "axon", date(2026, 5, 1), [old, new], vault=tmp_path
    ).read_text(encoding="utf-8")
    assert "[[dec-002]]" in text
    assert "[[dec-001]]" not in text
    assert "## Active" in text
