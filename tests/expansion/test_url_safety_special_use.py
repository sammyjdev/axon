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
        "http://[64:ff9b::a00:5]/",  # NAT64 well-known (RFC 6052) of private 10.0.0.5
        "http://[3fff::1]/",  # IPv6 documentation prefix (RFC 9637), is_global=True
    ],
)
def test_check_url_safety_rejects_6to4_special_use(url: str) -> None:
    # These special-use spaces slip past is_global on Python 3.11 (Codex
    # cross-review of PR #95). 2002::/16 and 3fff::/20 are on the explicit
    # denylist; 64:ff9b::a00:5 (NAT64, embeds private 10.0.0.5) is already caught
    # by the is_reserved check (it is inside ::/8) - kept here as a regression
    # guard so a future is_reserved change can't silently reopen it.
    with pytest.raises(ValueError):
        check_url_safety(url)
