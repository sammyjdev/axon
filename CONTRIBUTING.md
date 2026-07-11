# Contributing to AXON

Thanks for considering a contribution. AXON is an early-stage (Alpha) project;
expect some rough edges and fast-moving internals.

## Development setup

```bash
git clone https://github.com/sammyjdev/axon.git
cd axon
pip install -e ".[dev]"
pre-commit install   # ruff lint + gitleaks secret-scan on commit (.pre-commit-config.yaml)
```

## Before opening a PR

```bash
pytest tests/ -q
ruff check
python3 -m compileall src
```

All tests must pass. Fix `ruff` errors; warnings are at your discretion until
the broader lint scope is enforced in CI (see `.github/workflows/ci.yml`).

## CI workflow and artifacts

On every pull request, the CI runs security and validation checks including:
linting, compilation, tests, dependency audit, secret scanning, and dynamic
security testing (OWASP ZAP baseline scan). The ZAP scan report is available
as a CI artifact (`dast-zap-baseline-report`) in the Actions tab of the pull
request - use it to review potential security findings.

## Workflow

- TDD is expected: a failing test before production code, a regression test
  for bugfixes.
- Keep changes surgical — match existing style, avoid drive-by refactors in
  unrelated code.
- Commit messages: `<type>: <description>` (`feat`, `fix`, `refactor`, `docs`,
  `test`, `chore`, `perf`, `ci`).
- Reference the architectural decisions under `docs/decisions/` (`dec-*.md`)
  when a change touches an existing one; open a new one for decisions that
  aren't obvious from the code.

## Reporting bugs / requesting features

Open a GitHub issue. Use the provided issue templates when available.

## Reporting security issues

See [SECURITY.md](SECURITY.md) — do not open a public issue for
vulnerabilities.

## Project structure

See [CLAUDE.md](CLAUDE.md) (agent-facing) and
[docs/PROJECT_OVERVIEW.md](docs/PROJECT_OVERVIEW.md) for a map of the
subsystems, CLI surface, and MCP tools.
