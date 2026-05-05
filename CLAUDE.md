# Prometheus Agent Guide

This is the canonical agent context file for contributors working in this
repository. `AGENTS.md` points to this file.

## Project Overview

Prometheus is a self-hosted context engine for local knowledge retrieval,
context compression, and agent-facing workflows through a CLI (`pb`) and MCP.

The repository contains the engine and runtime code. User knowledge lives in an
external Markdown vault, typically configured through:

- `PROMETHEUS_ENGINE=/path/to/prometheus`
- `PROMETHEUS_VAULT=~/vault`

## Entry Points

- [README.md](README.md): public project overview and quick start
- [docs/VAULT_SETUP.md](docs/VAULT_SETUP.md): external vault bootstrap
- [docs/USAGE_GUIDE.md](docs/USAGE_GUIDE.md): CLI workflows
- [docs/ADR.md](docs/ADR.md): active architectural decisions
- [docs/ARD.md](docs/ARD.md): active architectural requirements

## Stable Architectural Decisions

### D1: Data and engine stay separate

- Vault data lives outside this repository.
- Runtime code and configuration live in this repository.
- Do not mix vault content into the engine tree.

### D2: Task-based cloud routing

| Task type | Default model |
| --- | --- |
| trivial/completion | `claude-haiku-4-5-20251001` |
| code analysis | `claude-sonnet-4-6` |
| architecture/deep reasoning | `claude-opus-4-7` |
| fallback | `claude-haiku-4-5-20251001` |

### D3: Local Ollama defaults

- `phi3:mini`: lightweight compression and local-first workflows
- `gemma4:e4b`: local scoring and classification
- `gemma4:26b`: heavier deep-suggestion workloads on larger hardware

### D4: Split graph backends

- Redis stores code dependency relationships.
- Neo4j is reserved for Mem0-style memory relationships.

### D5: Chunker quality is a release gate

- The Java chunker is a high-risk subsystem.
- Structure-aware chunking and fixture coverage must remain intact.
- Do not weaken chunker tests to make implementation changes pass.

## Code Conventions

- Python 3.11+ with type hints
- Prefer `dataclass` over ad-hoc dicts
- Prefer async for I/O-heavy paths
- Add comments only for non-obvious constraints or rationale
- Keep public examples and docs machine-agnostic
- `SessionStore` must be initialized explicitly with `.init()`

## Agent Rules

- Start from tests when changing behavior.
- Bugfixes should begin with a regression test when feasible.
- Features should have testable acceptance criteria before implementation.
- Do not silence failing tests or guardrails to make a change appear complete.
- Prefer the smallest coherent change that satisfies the behavior.

## Restricted Context Rules

- `work` is a restricted context.
- Never access restricted context implicitly.
- Use explicit `ctx=work` only when the task really requires it.
- Do not copy restricted or proprietary material into the repository or public
  documentation.

## Safety Rules

- Never commit credentials, tokens, `.env` files, or user data.
- Never move vault content into the engine repository.
- Never weaken isolation around restricted contexts as a shortcut.
- Investigate failing tests, hooks, or checks instead of bypassing them.

## Validation Defaults

Use `rtk` where available. Typical validation commands:

```bash
rtk pytest tests/ -q
rtk ruff check
rtk python3 -m compileall src
```

## RTK Notes

Prometheus is commonly used with RTK (Rust Token Killer) for compact command
output. Prefix commands with `rtk` when possible; if no specialized filter is
available, RTK passes the command through unchanged.
