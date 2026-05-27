"""Gate pipeline orchestrator for dec-111.

Runs the layers in order and returns a ``GateOutcome`` summarising
which layer passed/failed, whether structural mode was active, and
auxiliary metrics for the audit log.

Order matters: L1-light first (cheapest, deterministic), then L2,
then L3, then density. Structural detection runs once up front and
biases density thresholds.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from axon.adr.commit_context import CommitContext
from axon.adr.gates.density import passes_density
from axon.adr.gates.l1 import l1_light
from axon.adr.gates.l2 import passes_l2
from axon.adr.gates.l3 import passes_l3
from axon.adr.gates.structural import is_structural


class GateLayer(StrEnum):
    L1_LIGHT = "l1_light"
    L2 = "l2"
    L3 = "l3"
    DENSITY = "density"


@dataclass(frozen=True)
class ADRPayload:
    """Minimal ADR shape the gates need. Keeps gates decoupled from store.ADR."""

    title: str
    context: str
    decision: str
    rationale: str

    @property
    def text(self) -> str:
        return f"{self.title}\n\n{self.context}\n\n{self.decision}\n\n{self.rationale}"


@dataclass
class GateOutcome:
    """Result of running the full pipeline."""

    passed: bool
    failed_layer: GateLayer | None = None
    reason: str | None = None
    structural_mode: bool = False
    details: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class GateConfig:
    """Tunables from ``axon.toml#adr.*`` — passed in by the orchestrator's caller."""

    l2_min_overlap: int = 3
    l3_polarity_required: bool = True
    overlap_ratio_cap: float = 0.7
    overlap_ratio_cap_structural: float = 0.9
    l2_min_overlap_structural: int = 2
    repo_root: Path = field(default_factory=Path.cwd)


def evaluate(
    adr: ADRPayload,
    commit: CommitContext,
    config: GateConfig | None = None,
) -> GateOutcome:
    """Run the full pipeline. Stops at first failing layer."""
    cfg = config or GateConfig()

    structural = is_structural(commit)
    min_overlap = (
        cfg.l2_min_overlap_structural if structural else cfg.l2_min_overlap
    )
    polarity_required = cfg.l3_polarity_required and not structural

    # L1-light
    passed, l1_details = l1_light(adr.text, repo_root=cfg.repo_root)
    if not passed:
        return GateOutcome(
            passed=False,
            failed_layer=GateLayer.L1_LIGHT,
            reason="missing files or identifiers",
            structural_mode=structural,
            details=l1_details,
        )

    # Pool for L2/L3: diff + commit message body
    pool = f"{commit.diff}\n{commit.body}"

    # L2 lexical overlap
    passed, l2_count = passes_l2(
        adr.rationale, pool_text=pool, min_overlap=min_overlap
    )
    if not passed:
        return GateOutcome(
            passed=False,
            failed_layer=GateLayer.L2,
            reason=f"lexical overlap {l2_count} < {min_overlap}",
            structural_mode=structural,
            details={"overlap": l2_count, "min_overlap": min_overlap},
        )

    # L3 polarity
    passed, l3_matched = passes_l3(
        adr.title, adr.decision, pool_text=pool, required=polarity_required
    )
    if not passed:
        return GateOutcome(
            passed=False,
            failed_layer=GateLayer.L3,
            reason="no key term anchored in diff/body",
            structural_mode=structural,
            details={"matched_terms": l3_matched},
        )

    # Density
    passed, density_details = passes_density(
        adr.rationale,
        diff=commit.diff,
        structural_mode=structural,
        overlap_ratio_cap=cfg.overlap_ratio_cap,
        overlap_ratio_cap_structural=cfg.overlap_ratio_cap_structural,
    )
    if not passed:
        return GateOutcome(
            passed=False,
            failed_layer=GateLayer.DENSITY,
            reason=str(density_details.get("reason", "density check failed")),
            structural_mode=structural,
            details=density_details,
        )

    return GateOutcome(
        passed=True,
        structural_mode=structural,
        details={
            "l2_overlap": l2_count,
            "l3_matched": l3_matched,
            "density": density_details,
        },
    )


__all__ = [
    "ADRPayload",
    "GateConfig",
    "GateLayer",
    "GateOutcome",
    "evaluate",
]
