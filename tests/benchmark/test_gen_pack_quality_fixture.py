from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "gen_pack_quality_fixture.py"


def _load_module() -> Any:
    spec = importlib.util.spec_from_file_location("gen_pack_quality_fixture", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gen_mod = _load_module()
build_cases = gen_mod.build_cases
write_cases = gen_mod.write_cases


def test_build_cases_maps_row_to_case_structure() -> None:
    rows = [
        {
            "id": "dec-1",
            "summary": "why x breaks",
            "files": '["a.py","b.py"]',
        }
    ]
    assert build_cases(rows) == [
        {
            "query": "why x breaks",
            "expected_files": ["a.py", "b.py"],
            "decision_id": "dec-1",
        }
    ]


def test_write_cases_creates_file_and_roundtrips_content(tmp_path: Path) -> None:
    cases = [
        {
            "query": "why x breaks",
            "expected_files": ["a.py", "b.py"],
            "decision_id": "dec-1",
        }
    ]
    out_file = tmp_path / "fixture.json"
    write_cases(cases, out_file)
    assert out_file.is_file()
    assert json.loads(out_file.read_text(encoding="utf-8"))["cases"] == cases


def test_write_cases_creates_missing_parent_directory(tmp_path: Path) -> None:
    cases = [
        {
            "query": "why x breaks",
            "expected_files": ["a.py"],
            "decision_id": "dec-2",
        }
    ]
    out_file = tmp_path / "nested" / "parent" / "dir" / "fixture.json"
    assert not out_file.parent.exists()
    write_cases(cases, out_file)
    assert out_file.parent.is_dir()
    assert out_file.is_file()
    assert json.loads(out_file.read_text(encoding="utf-8"))["cases"] == cases


def test_query_is_bounded_by_a_cutoff_and_tie_broken_deterministically() -> None:
    """Without a cutoff, `ORDER BY created_at DESC LIMIT n` returns a different
    corpus every time a decision lands - the ruler retires itself in silence.
    And `created_at` alone is not a total order, so ties need a tiebreak.
    """
    query = gen_mod.QUERY
    assert "created_at <=" in query, "the corpus must be bounded by an explicit cutoff"
    assert "ORDER BY created_at DESC, id DESC" in query, "ties must break deterministically"


def test_write_cases_records_how_the_corpus_was_built() -> None:
    """Provenance travels with the fixture, so a later regeneration is checkable."""
    import tempfile

    cases = [{"query": "q", "expected_files": ["a.py"], "decision_id": "dec-1"}]
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "f.json"
        gen_mod.write_cases(cases, out, provenance={"cutoff": "2026-08-31T00:00:00Z", "limit": 120})
        payload = json.loads(out.read_text(encoding="utf-8"))

    assert payload["provenance"]["cutoff"] == "2026-08-31T00:00:00Z"
    assert payload["provenance"]["limit"] == 120
    assert payload["cases"] == cases


def test_load_cases_reads_the_provenance_wrapped_shape() -> None:
    import tempfile

    from axon.benchmark.pack_eval import load_cases

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "f.json"
        out.write_text(
            json.dumps(
                {
                    "provenance": {"cutoff": "2026-08-31T00:00:00Z", "limit": 1},
                    "cases": [
                        {"query": "q", "expected_files": ["a.py"], "decision_id": "dec-1"}
                    ],
                }
            ),
            encoding="utf-8",
        )
        cases = load_cases(out)

    assert len(cases) == 1
    assert cases[0].decision_id == "dec-1"


def test_load_cases_still_reads_a_bare_list() -> None:
    """The committed fixture predates the wrapper; it must keep loading."""
    import tempfile

    from axon.benchmark.pack_eval import load_cases

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "f.json"
        out.write_text(
            json.dumps([{"query": "q", "expected_files": ["a.py"], "decision_id": "dec-1"}]),
            encoding="utf-8",
        )
        assert len(load_cases(out)) == 1


def test_build_cases_strips_the_home_prefix_but_keeps_the_path() -> None:
    """Reducing an absolute path to its basename kept the username out of the
    fixture, and was metric-preserving while scoring was basename-based. Under
    path-suffix scoring it is not: `pyproject.toml` alone matches every repo.
    """
    files_json = json.dumps(
        ["/Users/samdev/.claude/docs/superpowers/specs/audit.md", "src/axon/auth.py"]
    )
    rows = [{"id": "dec-817", "summary": "ecosystem security audit", "files": files_json}]
    (case,) = build_cases(rows)
    assert case["expected_files"] == [
        ".claude/docs/superpowers/specs/audit.md",
        "src/axon/auth.py",
    ]


def test_build_cases_strips_a_linux_home_prefix_too() -> None:
    rows = [{"id": "d", "summary": "s", "files": json.dumps(["/home/someone/repo/a.py"])}]
    (case,) = build_cases(rows)
    assert case["expected_files"] == ["repo/a.py"]


def test_build_cases_leaks_no_username() -> None:
    rows = [{"id": "d", "summary": "s", "files": json.dumps(["/Users/samdev/x/y.py"])}]
    (case,) = build_cases(rows)
    assert "samdev" not in json.dumps(case)


def test_build_cases_keeps_an_absolute_path_outside_a_home_dir_identifiable() -> None:
    """A path with no home prefix has no username to hide; keep it specific."""
    rows = [{"id": "d", "summary": "s", "files": json.dumps(["/opt/tool/conf.yaml"])}]
    (case,) = build_cases(rows)
    assert case["expected_files"] == ["opt/tool/conf.yaml"]


# ── validity: a case the pipeline cannot possibly answer is not a case ───────


def test_unindexable_paths_are_recognised() -> None:
    """`.specs/` is gitignored and `docs/superpowers/plans/` is excluded by the
    pipeline on purpose - its comment says indexing eval artifacts "leaks answers
    into the measurement instrument". Charging retrieval for them measures the
    indexing policy, not the retrieval.
    """
    assert gen_mod.is_unindexable(".specs/features/issue-158/anneal.md") is True
    assert gen_mod.is_unindexable("docs/superpowers/plans/2026-08-22-plan.md") is True
    assert gen_mod.is_unindexable("src/axon/store/collections.py") is False
    assert gen_mod.is_unindexable("tests/conftest.py") is False


def test_docs_superpowers_specs_stay_valid() -> None:
    """Only plans/ is excluded by the pipeline; specs/ is indexed (342 rows)."""
    assert gen_mod.is_unindexable("docs/superpowers/specs/2026-08-29-design.md") is False


def test_build_cases_drops_a_case_with_no_reachable_file() -> None:
    rows = [
        {"id": "dec-a", "summary": "only unindexable files", "files": '[".specs/x/anneal.md"]'},
        {"id": "dec-b", "summary": "a reachable file", "files": '["src/axon/a.py"]'},
    ]
    ids = [case["decision_id"] for case in build_cases(rows)]
    assert ids == ["dec-b"]


def test_build_cases_keeps_a_mixed_case_but_prunes_the_unreachable_file() -> None:
    """A decision that touched both still scores - on the part that is reachable."""
    rows = [
        {
            "id": "dec-c",
            "summary": "mixed",
            "files": '[".specs/x/anneal.md", "src/axon/a.py"]',
        }
    ]
    (case,) = build_cases(rows)
    assert case["expected_files"] == ["src/axon/a.py"]
