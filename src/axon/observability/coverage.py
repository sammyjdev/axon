"""Retrieval coverage: the denominator the savings ratio is missing.

`aggregate_recall_savings` answers "how efficient is a search_code call?".
It cannot answer "how much did AXON actually save?", because an MCP server only
observes the calls it received - it is structurally blind to the file reads that
happened *instead of* a call. That denominator is collected in the agent's
harness (a PreToolUse(Read) hook) and written to opportunities.jsonl.

Coverage closes the gap: a 90% per-call ratio at 2% coverage is not 90% saved.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class CoverageAggregate:
    opportunities: int = 0
    searches: int = 0
    opportunity_tokens: int = 0
    # A read with no prior search means the tool was never reached for; a read
    # after a search means the search did not answer the question. Different
    # failures, different fixes - never pool them.
    reads_without_prior_search: int = 0
    reads_after_search: int = 0
    by_repo: Counter[str] = field(default_factory=Counter)

    @property
    def events(self) -> int:
        return self.opportunities + self.searches

    @property
    def coverage_ratio(self) -> float | None:
        if self.events == 0:
            return None
        return self.searches / self.events

    def delivered_savings(self, per_call_ratio: float) -> float | None:
        """Scale a per-call savings ratio by how often the tool was actually used."""
        coverage = self.coverage_ratio
        if coverage is None:
            return None
        return per_call_ratio * coverage


def _count_searches_since(file_path: Path, since: str | None) -> int:
    """Count search telemetry rows at or after `since`.

    The two logs do not start on the same day: search telemetry predates the
    harness-side collector. Counting the whole search history against a young
    opportunity log reports near-100% coverage, so the window is clamped to
    where BOTH sides were recording.
    """
    if not file_path.exists():
        return 0
    count = 0
    with file_path.open(encoding="utf-8") as fh:
        for raw_line in fh:
            if not raw_line.strip():
                continue
            if since is None:
                count += 1
                continue
            try:
                ts = str(json.loads(raw_line).get("ts", ""))
            except json.JSONDecodeError:
                continue
            if ts >= since:  # ISO-8601 UTC sorts lexicographically
                count += 1
    return count


def _first_timestamp(file_path: Path) -> str | None:
    if not file_path.exists():
        return None
    with file_path.open(encoding="utf-8") as fh:
        for raw_line in fh:
            if not raw_line.strip():
                continue
            try:
                return str(json.loads(raw_line).get("ts")) or None
            except json.JSONDecodeError:
                continue
    return None


def aggregate_coverage(
    opportunities_file: Path,
    chunks_file: Path,
) -> CoverageAggregate:
    """Combine harness-side opportunities with engine-side search telemetry."""
    if not opportunities_file.exists():
        return CoverageAggregate(searches=_count_searches_since(chunks_file, None))

    searches = _count_searches_since(chunks_file, _first_timestamp(opportunities_file))
    opportunities = 0
    tokens = 0
    without_search = 0
    after_search = 0
    by_repo: Counter[str] = Counter()

    with opportunities_file.open(encoding="utf-8") as fh:
        for raw_line in fh:
            if not raw_line.strip():
                continue
            try:
                record = json.loads(raw_line)
            except json.JSONDecodeError:
                continue  # a truncated tail line must not sink the report
            opportunities += 1
            tokens += int(record.get("est_tokens_full", 0))
            by_repo[str(record.get("repo", "?"))] += 1
            if int(record.get("searches_in_session", 0)) > 0:
                after_search += 1
            else:
                without_search += 1

    return CoverageAggregate(
        opportunities=opportunities,
        searches=searches,
        opportunity_tokens=tokens,
        reads_without_prior_search=without_search,
        reads_after_search=after_search,
        by_repo=by_repo,
    )
