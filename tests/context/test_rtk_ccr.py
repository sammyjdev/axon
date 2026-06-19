from __future__ import annotations

import pytest

from axon.context import rtk

# These exercise the real rtkx binary end-to-end; skip where it is not installed
# (CI bootstraps it via `axon rtk-install`).
pytestmark = pytest.mark.skipif(
    rtk.rtk_binary_path() is None,
    reason="rtkx binary not installed (run `axon rtk-install`)",
)


def test_store_then_restore_roundtrip() -> None:
    original = "def handle_retry(attempt: int) -> bool:\n    return attempt < 3\n" * 10
    handle = rtk.store_original_with_rtk(original)

    assert handle is not None
    assert len(handle) == 16

    restored = rtk.restore_original_with_rtk(handle)
    assert restored == original


def test_store_is_stable_for_same_content() -> None:
    text = "identical content for handle stability"
    h1 = rtk.store_original_with_rtk(text)
    h2 = rtk.store_original_with_rtk(text)
    assert h1 == h2


def test_restore_unknown_handle_raises() -> None:
    with pytest.raises(rtk.RTKError):
        rtk.restore_original_with_rtk("0000000000000000")
