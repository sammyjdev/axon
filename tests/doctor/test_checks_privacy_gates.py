from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from axon.cli.pb import app
from axon.doctor import CheckStatus
from axon.doctor.checks.privacy_gates import (
    check_restricted_root,
    check_work_projects,
)


@pytest.mark.parametrize("value", [None, "", "  "])
def test_an_unset_restricted_root_warns_that_restricted_sessions_will_be_imported(
    monkeypatch, value
) -> None:
    if value is None:
        monkeypatch.delenv("AXON_RESTRICTED_ROOT", raising=False)
    else:
        monkeypatch.setenv("AXON_RESTRICTED_ROOT", value)

    result = check_restricted_root()

    assert result.status is CheckStatus.WARN
    assert "AXON_RESTRICTED_ROOT" in result.detail


def test_a_set_restricted_root_is_ok(monkeypatch) -> None:
    monkeypatch.setenv("AXON_RESTRICTED_ROOT", "~/employer")

    assert check_restricted_root().status is CheckStatus.OK


@pytest.mark.parametrize("value", [None, "", " , "])
def test_unset_work_projects_warn_that_work_adrs_are_not_gated(monkeypatch, value) -> None:
    if value is None:
        monkeypatch.delenv("AXON_WORK_PROJECTS", raising=False)
    else:
        monkeypatch.setenv("AXON_WORK_PROJECTS", value)

    result = check_work_projects()

    assert result.status is CheckStatus.WARN
    assert "AXON_WORK_PROJECTS" in result.detail


def test_set_work_projects_are_ok(monkeypatch) -> None:
    monkeypatch.setenv("AXON_WORK_PROJECTS", "client-a")

    assert check_work_projects().status is CheckStatus.OK


def _unset_both(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AXON_ENGINE", str(tmp_path))
    monkeypatch.setenv("AXON_DATA_ROOT", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AXON_RESTRICTED_ROOT", raising=False)
    monkeypatch.delenv("AXON_WORK_PROJECTS", raising=False)
    # Every other check passes, so the exit code shows only the privacy warnings.
    monkeypatch.setattr("axon.doctor.run_all_checks", lambda **_kwargs: [])


def test_doctor_warns_about_both_open_gates_without_failing(monkeypatch, tmp_path) -> None:
    _unset_both(monkeypatch, tmp_path)

    result = CliRunner().invoke(app, ["doctor"])

    assert result.exit_code == 0, result.output
    assert "AXON_RESTRICTED_ROOT is unset" in result.stdout
    assert "AXON_WORK_PROJECTS is unset" in result.stdout


def test_doctor_ci_reports_both_open_gates(monkeypatch, tmp_path) -> None:
    _unset_both(monkeypatch, tmp_path)

    result = CliRunner().invoke(app, ["doctor", "--ci"])

    assert result.exit_code == 0, result.output
    by_name = {c["name"]: c for c in json.loads(result.stdout)["checks"]}
    assert by_name["privacy.restricted_root"]["status"] == "warn"
    assert by_name["privacy.work_projects"]["status"] == "warn"
