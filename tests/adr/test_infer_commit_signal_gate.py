"""E2E test for dec-110 signal gate in pb adr infer-commit."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from axon.cli.pb import app

runner = CliRunner()


def _git_outputs(commit_message: str, diff: str = "fake diff") -> dict[tuple, str]:
    """Build the subprocess return-value map keyed by argv tuple."""
    return {
        ("git", "log", "-1", "--pretty=%B"): commit_message,
        ("git", "log", "-1", "--pretty=%s"): commit_message.split("\n", 1)[0],
        ("git", "log", "-1", "--stat", "--pretty="): "src/x.py | 1 +",
        (
            "git",
            "diff",
            "HEAD~1",
            "HEAD",
            "--",
            ":(exclude)*.lock",
            ":(exclude)*.json",
        ): diff,
    }


def _fake_check_output_factory(outputs: dict[tuple, str]):
    def _fake(argv, text=True, **_kwargs):  # noqa: ANN001
        key = tuple(argv)
        if key in outputs:
            return outputs[key]
        raise subprocess.CalledProcessError(1, argv)

    return _fake


class TestSignalGate:
    def test_no_signal_short_circuits_before_llm(self) -> None:
        """Commit without arch:/decision:/trailer must not call LLM."""
        outputs = _git_outputs("fix: typo in README")

        # If signal gate works, litellm.acompletion is never imported/called.
        # We assert via subprocess being called only for git, not for LLM.
        with patch(
            "subprocess.check_output",
            side_effect=_fake_check_output_factory(outputs),
        ) as mock_subproc:
            result = runner.invoke(app, ["adr", "infer-commit", "--project", "p"])

        assert result.exit_code == 0
        # Only git was called; no LLM-related subprocess
        for call in mock_subproc.call_args_list:
            argv = call.args[0]
            assert argv[0] == "git", f"unexpected subprocess call: {argv}"

    def test_arch_prefix_triggers_inference_path(self) -> None:
        """With arch: prefix, gate passes — LLM call is attempted."""
        outputs = _git_outputs("arch: migrate to repository pattern")

        with (
            patch(
                "subprocess.check_output",
                side_effect=_fake_check_output_factory(outputs),
            ),
            patch("litellm.acompletion") as mock_llm,
        ):
            # Make the LLM return null so no DB write happens
            class _Resp:
                class _Choice:
                    class _Msg:
                        content = "null"

                    message = _Msg()

                choices = [_Choice()]

            async def _ac(*_a, **_kw):
                return _Resp()

            mock_llm.side_effect = _ac
            result = runner.invoke(app, ["adr", "infer-commit", "--project", "p"])

        assert result.exit_code == 0
        assert mock_llm.called, "LLM must be invoked when arch: signal present"

    def test_force_bypasses_gate(self) -> None:
        """--force should call LLM even without signal."""
        outputs = _git_outputs("fix: nothing arch about this")

        with (
            patch(
                "subprocess.check_output",
                side_effect=_fake_check_output_factory(outputs),
            ),
            patch("litellm.acompletion") as mock_llm,
        ):
            class _Resp:
                class _Choice:
                    class _Msg:
                        content = "null"

                    message = _Msg()

                choices = [_Choice()]

            async def _ac(*_a, **_kw):
                return _Resp()

            mock_llm.side_effect = _ac
            result = runner.invoke(
                app, ["adr", "infer-commit", "--project", "p", "--force"]
            )

        assert result.exit_code == 0
        assert mock_llm.called, "LLM must be invoked when --force is passed"

    def test_trailer_triggers_inference(self) -> None:
        outputs = _git_outputs(
            "fix: bump deps\n\nADR-Decision: pin transformers to 4.x"
        )

        with (
            patch(
                "subprocess.check_output",
                side_effect=_fake_check_output_factory(outputs),
            ),
            patch("litellm.acompletion") as mock_llm,
        ):
            class _Resp:
                class _Choice:
                    class _Msg:
                        content = "null"

                    message = _Msg()

                choices = [_Choice()]

            async def _ac(*_a, **_kw):
                return _Resp()

            mock_llm.side_effect = _ac
            result = runner.invoke(app, ["adr", "infer-commit", "--project", "p"])

        assert result.exit_code == 0
        assert mock_llm.called, "LLM must be invoked when trailer present"


@pytest.fixture(autouse=True)
def _isolate_db(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect DB path so the test doesn't write to a real engine dir."""
    monkeypatch.setenv("AXON_ENGINE", str(tmp_path))
