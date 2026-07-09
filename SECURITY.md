# Security Policy

## Reporting a Vulnerability

Please **do not** open a public GitHub issue for security vulnerabilities.

Instead, use
[GitHub Security Advisories](https://github.com/sammyjdev/axon/security/advisories/new)
to report privately. Include:

- A description of the vulnerability and its impact
- Steps to reproduce (a minimal repro is ideal)
- Affected version / commit

We aim to acknowledge reports within a few days. Once a fix is available, a
security advisory will be published and credited to the reporter (unless you
prefer to stay anonymous).

## Supported Versions

AXON is pre-1.0 (Alpha). Only the latest release on `master` is supported;
there is no backport policy yet.

## Scope

AXON runs locally and integrates with cloud LLM providers (Groq, NVIDIA NIM,
OpenRouter) via API keys supplied through environment variables. Relevant
security surface includes:

- Secret handling (`.env`, provider API keys)
- The `ctx=work` restricted-context isolation (see `docs/decisions/dec-109-*.md`)
- MCP tool risk gating (`read` / `write` / `destructive`, see ADR-013)
- Git hook installation (`axon hooks install`)

Issues outside this scope (e.g. vulnerabilities in a pinned third-party
dependency) should be reported upstream, but feel free to flag them here too
if they affect AXON's default configuration.
