"""The eval scripts' default fixtures ship with them.

Deleting tests/benchmark took scripts/eval_exact_terms.py's fixture with it, and
the script failed on its first line of work with FileNotFoundError.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


@pytest.mark.parametrize("script", ["eval_nl_code_recall.py", "eval_exact_terms.py"])
def test_an_eval_scripts_default_fixture_exists(script: str) -> None:
    spec = importlib.util.spec_from_file_location(script.removesuffix(".py"), SCRIPTS / script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.FIXTURE.is_file()
