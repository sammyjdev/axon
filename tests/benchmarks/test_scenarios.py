from benchmarks.scenarios import long_session_axon, long_session_baseline


def test_baseline_run_reports_total(capsys):
    total = long_session_baseline.run()
    out = capsys.readouterr().out
    assert total > 0
    assert "baseline" in out.lower()
    assert str(total) in out


def test_axon_run_reports_total_and_savings(capsys):
    result = long_session_axon.run()
    out = capsys.readouterr().out
    assert result["axon_total"] > 0
    assert result["baseline_total"] > result["axon_total"]
    assert 0.0 < result["savings"] < 1.0
    assert "savings" in out.lower()
