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
