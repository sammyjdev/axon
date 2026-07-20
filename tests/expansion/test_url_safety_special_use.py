from __future__ import annotations

import pytest

from axon.expansion.url_safety import check_url_safety


@pytest.mark.parametrize(
    "url",
    [
        "http://192.0.0.8/",  # IETF protocol assignment (used for NAT64/DNS64 discovery)
        "http://192.0.0.170/",  # NAT64 well-known prefix address, same /24
    ],
)
def test_check_url_safety_rejects_192_0_0_0_24(url: str) -> None:
    with pytest.raises(ValueError):
        check_url_safety(url)
