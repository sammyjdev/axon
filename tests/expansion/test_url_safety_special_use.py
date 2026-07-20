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


@pytest.mark.parametrize(
    "url",
    [
        "http://192.88.99.2/",  # 6to4 relay anycast (RFC 7526), is_global=True on 3.11
        "http://[2002:0a00:0005::]/",  # 6to4 encoding of private 10.0.0.5, is_global=True
    ],
)
def test_check_url_safety_rejects_6to4_special_use(url: str) -> None:
    # Both slip past is_global/is_multicast/is_reserved on Python 3.11 (Codex
    # cross-review of PR #95). 6to4 relay anycast can pivot to a relay, and the
    # 2002::/16 space embeds an arbitrary IPv4 (here a private one) in the address.
    with pytest.raises(ValueError):
        check_url_safety(url)
