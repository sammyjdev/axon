# Audit — bare `except Exception:` handlers

Status: audit (Phase 0.4). Catalog only — no code changed by this document.

24 occurrences across 10 modules. Each should later be narrowed to a specific
exception type or routed through the typed exceptions from `axon/exceptions.py`
(Phase 1, T1.4). This document catalogs; it does not fix.

## Catalog

| Module:line | Context |
| --- | --- |
| `expansion/service.py:190` | reindex / pipeline fallback |
| `expansion/service.py:303` | reindex / pipeline fallback |
| `expansion/service.py:308` | nested fallback |
| `expansion/service.py:332` | reindex / pipeline fallback |
| `expansion/service.py:337` | nested fallback |
| `expansion/service.py:555` | reindex / pipeline fallback |
| `cli/pb.py:315` | CLI error recovery |
| `cli/pb.py:1294` | CLI error recovery (`as e`) |
| `cli/pb.py:1500` | CLI error recovery |
| `cli/pb.py:1836` | CLI error recovery (`as e`) |
| `cli/pb.py:2237` | CLI error recovery |
| `resilience/circuit_breaker.py:9` | optional-import guard (`# pragma: no cover`) |
| `resilience/circuit_breaker.py:43` | breaker state read fallback |
| `resilience/circuit_breaker.py:106` | breaker state read fallback |
| `resilience/circuit_breaker.py:123` | breaker state read fallback |
| `router/engine.py:93` | routing fallback |
| `router/engine.py:224` | `daily_cost()` fallback |
| `router/classifier.py:90` | classifier fallback |
| `router/classifier.py:115` | classifier fallback |
| `router/compressor.py:83` | caveman compression (`# noqa: BLE001`) |
| `watcher/main.py:73` | file-processing fallback |
| `vault/deep_suggester.py:48` | suggestion fallback |
| `mcp/server.py:195` | MCP handler fallback |
| `expansion/scoring.py:103` | scoring fallback |

## Classification

- **Graceful degradation (acceptable, low priority):** `circuit_breaker.py`,
  `router/*`, `watcher/main.py`, `mcp/server.py` — these intentionally swallow
  to keep a non-critical path alive. Narrow to specific types where cheap.
- **Should be narrowed (higher priority):** `expansion/service.py` (6 sites,
  some nested) and `cli/pb.py` (5 sites) — broad catches here can hide real
  bugs. Route through typed exceptions once `axon/exceptions.py` lands.

## Recommendation

Do not fix during Phase 0. After Phase 1 T1.4 (`axon/exceptions.py`), open
focused follow-ups module-by-module, starting with `expansion/service.py`.
