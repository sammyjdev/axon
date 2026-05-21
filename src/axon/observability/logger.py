"""structlog logging configuration for AXON.

JSON output in production (``AXON_ENV=prod``), colored console in development.
Every record is also written as JSON lines to a daily log file. The log
directory defaults to ``~/.axon/logs`` and can be overridden with
``AXON_LOG_DIR`` (used by tests).
"""

from __future__ import annotations

import logging
import os
from datetime import date
from pathlib import Path

import structlog

_configured = False


def _log_path() -> Path:
    log_dir = Path(os.environ.get("AXON_LOG_DIR", str(Path.home() / ".axon" / "logs")))
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / f"axon-{date.today().isoformat()}.jsonl"


def _configure() -> None:
    global _configured
    if _configured:
        return

    pre_chain = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    strip_meta = structlog.stdlib.ProcessorFormatter.remove_processors_meta
    console_renderer = (
        structlog.processors.JSONRenderer()
        if os.environ.get("AXON_ENV") == "prod"
        else structlog.dev.ConsoleRenderer(colors=True)
    )

    stream = logging.StreamHandler()
    stream.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=pre_chain,
            processors=[strip_meta, console_renderer],
        )
    )
    file_handler = logging.FileHandler(_log_path(), encoding="utf-8")
    file_handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=pre_chain,
            processors=[strip_meta, structlog.processors.JSONRenderer()],
        )
    )

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(stream)
    root.addHandler(file_handler)
    root.setLevel(logging.INFO)

    structlog.configure(
        processors=[*pre_chain, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    _configured = True


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a configured structlog logger bound to ``name``."""
    _configure()
    return structlog.get_logger(name)
