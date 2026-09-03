from __future__ import annotations

import json
from pathlib import Path

import pytest


def test_basenames_strips_directories() -> None:
    from axon.benchmark.pack_eval import basenames

    assert basenames(["src/axon/store/collections.py", "a/b/x.md"]) == {
        "collections.py",
        "x.md",
    }


def test_pack_hit_is_true_when_any_expected_file_is_present() -> None:
    from axon.benchmark.pack_eval import pack_hit

    assert pack_hit({"a.py", "b.py"}, ["z.py", "a.py"]) is True


def test_pack_hit_is_false_when_none_present() -> None:
    from axon.benchmark.pack_eval import pack_hit

    assert pack_hit({"a.py"}, ["z.py", "y.py"]) is False


def test_pack_coverage_is_the_fraction_of_expected_files_found() -> None:
    from axon.benchmark.pack_eval import pack_coverage

    assert pack_coverage({"a.py", "b.py", "c.py"}, ["a.py", "c.py", "z.py"]) == pytest.approx(2 / 3)


def test_pack_coverage_of_empty_expectation_is_zero_not_a_crash() -> None:
    from axon.benchmark.pack_eval import pack_coverage

    assert pack_coverage(set(), ["a.py"]) == 0.0


def test_duplicate_hits_do_not_inflate_coverage() -> None:
    """A pack repeating one file must not score as if it found two."""
    from axon.benchmark.pack_eval import pack_coverage

    assert pack_coverage({"a.py", "b.py"}, ["a.py", "a.py", "a.py"]) == pytest.approx(0.5)


def test_load_cases_reads_the_committed_fixture(tmp_path: Path) -> None:
    from axon.benchmark.pack_eval import load_cases

    fixture = tmp_path / "cases.json"
    fixture.write_text(
        json.dumps(
            [{"query": "why does x break", "expected_files": ["a.py"], "decision_id": "dec-1"}]
        ),
        encoding="utf-8",
    )
    cases = load_cases(fixture)
    assert len(cases) == 1
    assert cases[0].query == "why does x break"
    assert cases[0].expected_files == ["a.py"]


def test_segment_file_paths_reads_the_path_out_of_an_assembled_segment() -> None:
    """issue #178: the eval must be able to score the pack, not only the hits."""
    from axon.benchmark.pack_eval import segment_file_paths

    segments = (
        "### delete_by_file (python)\n"
        "Arquivo: src/axon/store/pg_vector_store.py\n"
        "Score: 0.553\n"
        "Trecho: async def delete_by_file(...)",
        "### dedup_hits (python)\nArquivo: src/axon/context/pack_dedup.py\nScore: 0.501\nTrecho: x",
    )
    assert segment_file_paths(segments) == [
        "src/axon/store/pg_vector_store.py",
        "src/axon/context/pack_dedup.py",
    ]


def test_segment_file_paths_ignores_the_word_in_the_excerpt_body() -> None:
    """_build_context_pack flattens newlines out of content, so only the real
    header line starts with Arquivo:."""
    from axon.benchmark.pack_eval import segment_file_paths

    segments = ("### s (python)\nArquivo: a.py\nScore: 0.5\nTrecho: veja Arquivo: b.py no doc",)
    assert segment_file_paths(segments) == ["a.py"]


def test_segment_file_paths_on_no_segments_is_empty() -> None:
    from axon.benchmark.pack_eval import segment_file_paths

    assert segment_file_paths(()) == []


# ── ruler precision: match on the relative path, not the basename ───────────


def test_found_expected_matches_a_relative_path_against_an_absolute_hit() -> None:
    from axon.benchmark.pack_eval import found_expected

    found = found_expected(
        {"src/axon/store/collections.py"},
        ["/Users/x/dev/axon/src/axon/store/collections.py"],
    )
    assert found == {"src/axon/store/collections.py"}


def test_found_expected_does_not_credit_a_same_named_file_elsewhere() -> None:
    """The whole point: __init__.py appears 64 times in the corpus, and basename
    matching credited any of them for the one that was expected."""
    from axon.benchmark.pack_eval import found_expected

    assert found_expected(
        {"src/axon/store/__init__.py"},
        ["/Users/x/dev/axon/src/axon/embedder/__init__.py"],
    ) == set()


def test_found_expected_requires_a_segment_boundary() -> None:
    """`store/collections.py` must not be satisfied by `datastore/collections.py`."""
    from axon.benchmark.pack_eval import found_expected

    assert found_expected(
        {"store/collections.py"},
        ["/Users/x/dev/axon/src/axon/datastore/collections.py"],
    ) == set()


def test_found_expected_handles_an_exact_equal_path() -> None:
    from axon.benchmark.pack_eval import found_expected

    assert found_expected({"a/b.py"}, ["a/b.py"]) == {"a/b.py"}


def test_pack_hit_and_coverage_use_path_matching() -> None:
    from axon.benchmark.pack_eval import pack_coverage, pack_hit

    expected = {"src/a/x.py", "src/b/y.py"}
    hits = ["/repo/src/a/x.py", "/repo/src/c/y.py"]  # y.py is the WRONG y.py
    assert pack_hit(expected, hits) is True
    assert pack_coverage(expected, hits) == pytest.approx(0.5)
