# Migration plan — stdlib `logging` → structlog

Status: audit (Phase 0.4). No code changed by this document.

## Current state

The engine uses the standard-library `logging` module. No central logging
configuration exists (no `basicConfig`, `dictConfig`, handlers, or levels set in
`src/`). Each module does `logger = logging.getLogger(__name__)` and emits ad-hoc
records; output formatting is left to whatever the host process configures.

## Affected modules (8)

| Module | Notes |
| --- | --- |
| `src/axon/memory/session_compressor.py` | compression progress / errors |
| `src/axon/memory/mem0_tool.py` | mem0 calls, context-gating warnings |
| `src/axon/memory/session_hook.py` | vault daily-note writes |
| `src/axon/watcher/main.py` | file-watch / indexing events |
| `src/axon/embedder/pipeline.py` | ingestion progress |
| `src/axon/observability/compliance.py` | compliance events |
| `src/axon/router/engine.py` | routing / cost / breaker logs |
| `src/axon/router/compressor.py` | caveman compression logs |

## Target

`src/axon/observability/logger.py` (Phase 1, T1.5):

- structlog with JSON output when `AXON_ENV=prod`, colored console in dev.
- `get_logger(name)` factory.
- File sink at `~/.axon/logs/axon-YYYY-MM-DD.jsonl` with daily rotation.

## Migration steps

1. Land `observability/logger.py` (T1.5) with the structlog config.
2. Per module: replace `import logging` + `logging.getLogger(__name__)` with
   `from axon.observability.logger import get_logger` + `get_logger(__name__)`.
3. Convert positional/format-string log calls to structlog key-value style
   (`logger.info("event", key=value)`).
4. Verify the regression suite stays green; tests should not assert on log text.

## Effort

Low/mechanical per module; the only design work is the `logger.py` factory.
Recommend doing it alongside T1.5 in Phase 1 rather than as a separate pass.
