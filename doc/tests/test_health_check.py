"""Tests for AXON ecosystem health checks."""

import sys
from pathlib import Path

# Add scripts directory to path
scripts_dir = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(scripts_dir))

from checks.versioning import VersioningCheck
from checks.coupling import CouplingCheck
from checks.drift import DriftCheck
from checks.performance import PerformanceCheck
from checks.ci_status import CIStatusCheck


def test_versioning_check_runs():
    """Validate versioning checks complete without crashing."""
    check = VersioningCheck()
    result = check.run()

    assert isinstance(result, dict)
    assert "glyph_eval_dependency_free" in result
    assert "glyph_pin_in_axon" in result
    assert "forge_axon_dep" in result

    for metric_name, metric_value in result.items():
        assert isinstance(metric_value, dict), f"{metric_name} is not a dict"
        assert "status" in metric_value, f"{metric_name} missing 'status'"
        assert "value" in metric_value, f"{metric_name} missing 'value'"
        assert "severity" in metric_value, f"{metric_name} missing 'severity'"


def test_coupling_check_runs():
    """Validate coupling checks complete without crashing."""
    check = CouplingCheck()
    result = check.run()

    assert isinstance(result, dict)
    assert "axon_imports_glyph" in result
    assert "glyph_imports_axon" in result
    assert "gnomon_imports_axon" in result
    assert "forge_imports_axon" in result

    for metric_name, metric_value in result.items():
        assert isinstance(metric_value, dict), f"{metric_name} is not a dict"
        assert "status" in metric_value, f"{metric_name} missing 'status'"


def test_drift_check_runs():
    """Validate drift checks complete without crashing."""
    check = DriftCheck()
    result = check.run()

    assert isinstance(result, dict)
    assert "forge_vs_axon_router" in result

    forge_check = result["forge_vs_axon_router"]
    assert isinstance(forge_check, dict)
    assert "status" in forge_check

    if forge_check["status"] != "missing":
        assert "diff_percent" in forge_check or "error" in forge_check


def test_performance_check_runs():
    """Validate performance checks complete without crashing."""
    check = PerformanceCheck()
    result = check.run()

    assert isinstance(result, dict)
    assert "glyph_graph_build_latency_ms" in result
    assert "glyph_cache_hit_rate" in result

    for metric_value in result.values():
        assert isinstance(metric_value, dict)
        assert "status" in metric_value


def test_ci_status_check_runs():
    """Validate CI status checks complete without crashing."""
    check = CIStatusCheck()
    result = check.run()

    assert isinstance(result, dict)
    assert "axon" in result
    assert "glyph" in result
    assert "gnomon" in result
    assert "forge" in result

    for proj_name, proj_result in result.items():
        assert isinstance(proj_result, dict)
        assert "status" in proj_result


def test_all_status_values_valid():
    """Validate that all status values are from allowed set."""
    allowed_statuses = {"ok", "warning", "critical", "missing", "unknown", "error"}

    checks = [
        VersioningCheck(),
        CouplingCheck(),
        DriftCheck(),
        PerformanceCheck(),
        CIStatusCheck(),
    ]

    for check_instance in checks:
        result = check_instance.run()
        for category_name, category_result in result.items():
            if isinstance(category_result, dict):
                for metric_name, metric_value in category_result.items():
                    if isinstance(metric_value, dict) and "status" in metric_value:
                        status = metric_value["status"]
                        assert (
                            status in allowed_statuses
                        ), f"{category_name}/{metric_name} has invalid status: {status}"


if __name__ == "__main__":
    # Run tests
    test_versioning_check_runs()
    print("✓ test_versioning_check_runs")

    test_coupling_check_runs()
    print("✓ test_coupling_check_runs")

    test_drift_check_runs()
    print("✓ test_drift_check_runs")

    test_performance_check_runs()
    print("✓ test_performance_check_runs")

    test_ci_status_check_runs()
    print("✓ test_ci_status_check_runs")

    test_all_status_values_valid()
    print("✓ test_all_status_values_valid")

    print("\n✓ All tests passed!")
