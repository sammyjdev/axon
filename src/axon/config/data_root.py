"""Single source of truth for the local AXON data root.

Multiple subsystems (SessionStore fallback, ADR draft pool, audit log,
doctor checks) all need to resolve the on-disk location where AXON keeps
its per-repo state (``.axon/`` by default, override via
``AXON_DATA_ROOT``). Centralising the lookup avoids drift when the
default ever needs to change.

This is intentionally a tiny, dependency-free module so it can be
imported from anywhere without circular-import risk.
"""

from __future__ import annotations

import os
from pathlib import Path

_DEFAULT = ".axon"
_ENV_VAR = "AXON_DATA_ROOT"


def data_root() -> Path:
    """Return the directory AXON uses for local per-repo state.

    Resolves ``$AXON_DATA_ROOT`` if set, otherwise ``.axon`` relative to
    the current working directory. Callers should treat the result as
    advisory — directories are created lazily by the subsystems that
    write into them.
    """
    return Path(os.environ.get(_ENV_VAR, _DEFAULT))
