from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SetupSession:
    transport: str = "stdio"
    http_host: str | None = None
    http_port: int | None = None
    vault_contexts: tuple[str, ...] = field(default_factory=tuple)
    include_work_context: bool = False
