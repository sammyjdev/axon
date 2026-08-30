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
    assert json.loads(out_file.read_text(encoding="utf-8")) == cases


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
    assert json.loads(out_file.read_text(encoding="utf-8")) == cases
