#!/usr/bin/env python3
"""AXON Ecosystem health check — collect and report metrics."""

import json
import sys
from pathlib import Path
from datetime import datetime

# Add checks directory to path
checks_dir = Path(__file__).parent / "checks"
sys.path.insert(0, str(checks_dir.parent))

from checks.versioning import VersioningCheck
from checks.coupling import CouplingCheck
from checks.drift import DriftCheck
from checks.performance import PerformanceCheck
from checks.ci_status import CIStatusCheck


def determine_overall_status(metrics: dict) -> str:
    """Determine overall health based on all metrics."""
    has_critical = False
    has_warning = False

    for category in metrics.values():
        for check_result in category.values():
            if isinstance(check_result, dict):
                status = check_result.get("status")
                if status == "critical":
                    has_critical = True
                elif status == "warning":
                    has_warning = True

    if has_critical:
        return "critical"
    elif has_warning:
        return "warning"
    else:
        return "healthy"


def main():
    """Run all health checks and output JSON."""
    try:
        metrics = {
            "versioning": VersioningCheck().run(),
            "coupling": CouplingCheck().run(),
            "drift": DriftCheck().run(),
            "performance": PerformanceCheck().run(),
            "ci_status": CIStatusCheck().run(),
        }
    except Exception as e:
        print(f"ERROR: Failed to collect metrics: {e}", file=sys.stderr)
        return 2

    overall_status = determine_overall_status(metrics)

    output = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "overall_status": overall_status,
        "metrics": metrics,
    }

    # Write to file
    doc_dir = Path(__file__).parent.parent
    metrics_file = doc_dir / "docs" / "metrics" / "ecosystem-health.json"
    metrics_file.parent.mkdir(parents=True, exist_ok=True)

    try:
        metrics_file.write_text(json.dumps(output, indent=2))
        print(f"✓ Metrics saved to {metrics_file}", file=sys.stderr)
    except Exception as e:
        print(f"ERROR: Failed to write metrics file: {e}", file=sys.stderr)
        return 2

    # Print to stdout
    print(json.dumps(output, indent=2))

    # Return appropriate exit code
    if overall_status == "critical":
        return 2
    elif overall_status == "warning":
        return 1
    else:
        return 0


if __name__ == "__main__":
    sys.exit(main())
