# dec-108 - Complete Prometheus -> AXON rename (closes dec-100 followups)

- Status: accepted
- Date: 2026-05-25

## Context

dec-100 (2026-05-21) renamed the Python package, distribution, and CLI from
Prometheus to AXON. Three consequences were left as explicit follow-ups:

1. `prometheus.toml` was not renamed to `axon.toml`
2. `PROMETHEUS_*` env vars without a compat fallback (had already been removed)
3. Local path `~/dev/Prometheus` not renamed (operator's responsibility)

In addition, the mechanical sweep in dec-100 left ~250 residual "Prometheus"/"prometheus"
references in docstrings, help text, configs, and tests.

## Decision

Complete the rename from dec-100 in a single pass:

- **Cat A - text/display**: docstrings, echo messages, help text, READMEs.
  `s/Prometheus/AXON/` in display contexts.
- **Cat B - identifiers**:
  - `get_prometheus_config_path()` → `get_axon_config_path()`
  - `prometheus.toml` → `axon.toml`
  - `~/dev/Prometheus` → `~/dev/axon` (default in `.env.example` and `runtime.py`)
  - Test function names referencing `prometheus_toml` renamed
  - Serialization kind `"config/prometheus_toml"` → `"config/axon_toml"` in
    the exporter and in related tests
- **Cat C - external infra**:
  - `POSTGRES_USER` default in `docker-compose.yml` and `.env.example`:
    `prometheus` → `axon`
  - HTTP User-Agent `PrometheusExpansion/1.0` → `AxonExpansion/1.0`

### Compat shim for legacy `prometheus.toml`

`get_axon_config_path()` in `src/axon/config/runtime.py` now:

1. Honors `AXON_CONFIG` (env override) if set
2. New default: `./axon.toml`
3. **Fallback**: if `./axon.toml` does not exist but `./prometheus.toml` does,
   reads the legacy file silently

No hard warning to avoid polluting the CLI; migration is `mv prometheus.toml axon.toml`
when the user is ready.

### What was NOT renamed

- `docs/decisions/dec-*.md` (history)
- `docs/superpowers/specs/2026-05-18-prometheus-second-brain-design.md`
  (historical spec of the original design)
- `src/axon/store/graph_namespace.py` and its test - dead code marked in
  dec-101 (Neo4j dropped); the literal namespace `"prometheus"` in the test
  preserved as irrelevant
- Postgres volumes already created under user `prometheus` in existing
  installations (compat via explicit env var)

## Rationale

- Inconsistency between code and project name pollutes tier-1 onboarding and
  documentation.
- `prometheus.toml` was the last real friction point of the rename - the rest
  was cosmetic but provided no value.
- The compat shim handles local migration without forcing the user to rename
  the file in the same PR that pulls.

## Consequences

- Users with a local `prometheus.toml` continue to work. When they manually
  rename it to `axon.toml`, the shim gets out of the way.
- Postgres volumes with `POSTGRES_USER=prometheus` already provisioned
  continue working if `.env.local` keeps the override; the new default is
  `axon` but the env override takes precedence.
- Trace logs / observability that filter by User-Agent
  `PrometheusExpansion` need to update to `AxonExpansion`.
- Chunker test fixtures (`tests/embedder/fixtures/python/router.py`)
  preserved intact (ADR-005/D5).

## Migration

For existing users:

```bash
git pull
# If you have a local prometheus.toml, the engine still reads it. Rename it
# to axon.toml whenever you want:
mv prometheus.toml axon.toml

# If you had .env.local pointing AXON_ENGINE=~/dev/Prometheus, that path
# stays valid - the repo does not depend on the local directory name.
# Renaming the directory is optional.

# If you previously brought up Postgres with the 'prometheus' user, keep
# .env.local with POSTGRES_USER=prometheus. Fresh installs already pick
# 'axon' as the default.
```

dec-100 is closed as of this decision; rename follow-ups fall under this dec.
