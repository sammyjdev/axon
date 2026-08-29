"""A rejected compression must not look like a compression that gained nothing.

Every real compression event from 2026-06-26 to 2026-08-29 recorded
`reduction_pct = 0.0` on a live engine. That was not the compressor failing to
save tokens - it was `caveman_compress_guarded` rejecting its own output twice
and returning the original text, while the note explaining why stopped at the
caller and never reached telemetry.

Two months of "0.0%" that read as a weak result and were actually a broken
guard. The published p50 of 85.5% describes the June window that closed before
it started (#168).
"""

from __future__ import annotations

from axon.observability.compression_telemetry import CompressionRecord


def _record(**kwargs) -> CompressionRecord:
    base = {
        "ts": "2026-08-29T00:00:00Z",
        "engine": "caveman/phi3+rtkx",
        "caller": "mcp",
        "ctx": None,
        "before_tokens": 1000,
        "after_tokens": 1000,
        "reduction_tokens": 0,
        "reduction_pct": 0.0,
    }
    return CompressionRecord(**{**base, "kind": "compression", **kwargs})


def test_a_rejection_carries_its_reason() -> None:
    rejected = _record(rejection_note="fallback_to_full_context: empty_output")

    assert rejected.rejection_note, (
        "a guarded rejection must say why - without it the row is identical to "
        "a compression that ran and saved nothing"
    )


def test_a_genuine_zero_gain_is_distinguishable_from_a_rejection() -> None:
    """Same numbers, different meaning. Only the note separates them."""
    rejection = _record(rejection_note="prompt_contamination")
    no_gain = _record()

    assert rejection.reduction_pct == no_gain.reduction_pct == 0.0
    assert rejection.rejection_note != no_gain.rejection_note
    assert no_gain.rejection_note is None


def test_expansion_is_recorded_as_negative_not_floored() -> None:
    """A compressor that makes text longer must be visible as such.

    `max(0, before - after)` reported expansion as zero, which is the same value
    a no-op produces. The reader could not tell "did nothing" from "made it
    worse".
    """
    expanded = _record(
        before_tokens=1000,
        after_tokens=1200,
        reduction_tokens=-200,
        reduction_pct=-20.0,
    )

    assert expanded.reduction_tokens < 0
    assert expanded.reduction_pct < 0
