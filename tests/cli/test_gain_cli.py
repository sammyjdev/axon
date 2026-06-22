import json

from typer.testing import CliRunner

from axon.__main__ import app
from axon.observability.gain import GainSummary

runner = CliRunner()


def test_gain_populated_summary(monkeypatch):
    """Test gain command with a populated summary."""
    summary = GainSummary(
        windows=69,
        compressed=7,
        before_tokens=75258,
        after_tokens=15778,
        saved_tokens=59480,
        p50_pct=85.5,
        mean_pct=78.8,
        p95_pct=95.5,
        max_pct=97.0,
        by_engine={"caveman/phi3+rtkx": 60, "caveman/phi3": 9},
        daily_saved=[
            ("2026-06-15", 1000),
            ("2026-06-16", 5000),
            ("2026-06-17", 8000),
            ("2026-06-18", 12000),
            ("2026-06-19", 10000),
            ("2026-06-20", 23480),
        ],
    )

    def fake_load_gain(runtime=None):
        return summary

    monkeypatch.setattr("axon.observability.gain.load_gain", fake_load_gain)
    result = runner.invoke(app, ["gain"])
    assert result.exit_code == 0
    assert "AXON — context savings" in result.stdout
    assert "69" in result.stdout
    assert "59,480 tokens" in result.stdout
    assert "75,258" in result.stdout
    assert "15,778" in result.stdout
    assert "caveman/phi3+rtkx" in result.stdout
    assert "caveman/phi3" in result.stdout


def test_gain_empty_summary(monkeypatch):
    """Test gain command with empty summary (no compression records)."""
    summary = GainSummary(
        windows=0,
        compressed=0,
        before_tokens=0,
        after_tokens=0,
        saved_tokens=0,
        p50_pct=None,
        mean_pct=None,
        p95_pct=None,
        max_pct=None,
        by_engine={},
        daily_saved=[],
    )

    def fake_load_gain(runtime=None):
        return summary

    monkeypatch.setattr("axon.observability.gain.load_gain", fake_load_gain)
    result = runner.invoke(app, ["gain"])
    assert result.exit_code == 0
    assert "No compression telemetry yet" in result.stdout
    assert "Run some compressions first" in result.stdout


def test_gain_json_output(monkeypatch):
    """Test gain command with --json flag."""
    summary = GainSummary(
        windows=69,
        compressed=7,
        before_tokens=75258,
        after_tokens=15778,
        saved_tokens=59480,
        p50_pct=85.5,
        mean_pct=78.8,
        p95_pct=95.5,
        max_pct=97.0,
        by_engine={"caveman/phi3+rtkx": 60, "caveman/phi3": 9},
        daily_saved=[("2026-06-20", 23480)],
    )

    def fake_load_gain(runtime=None):
        return summary

    monkeypatch.setattr("axon.observability.gain.load_gain", fake_load_gain)
    result = runner.invoke(app, ["gain", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["windows"] == 69
    assert data["compressed"] == 7
    assert data["saved_tokens"] == 59480
    assert data["by_engine"]["caveman/phi3+rtkx"] == 60


def test_gain_none_percentiles(monkeypatch):
    """Test gain command when all percentiles are None."""
    summary = GainSummary(
        windows=10,
        compressed=0,
        before_tokens=1000,
        after_tokens=1000,
        saved_tokens=0,
        p50_pct=None,
        mean_pct=None,
        p95_pct=None,
        max_pct=None,
        by_engine={"caveman/phi3": 10},
        daily_saved=[("2026-06-20", 0)],
    )

    def fake_load_gain(runtime=None):
        return summary

    monkeypatch.setattr("axon.observability.gain.load_gain", fake_load_gain)
    result = runner.invoke(app, ["gain"])
    assert result.exit_code == 0
    assert "ratio       n/a" in result.stdout
