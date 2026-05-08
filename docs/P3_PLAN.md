# P3 Execution Plan

Status: planned next batch after `P2`

This plan turns the `P3` roadmap into execution-ready slices for parallel
agents. The goal is to make Prometheus extensible without collapsing back into
one author-specific layout or one agent-specific workflow.

## P3 Goals

- make domain-specific behavior installable instead of hardcoded
- make local state portable across machines
- make extension points explicit for tools and integrations
- make regressions measurable with fixed benchmarks

## Batch Structure

### P3-A1 Domain Packs

Scope:
- define pack layout under a neutral directory such as `domains/` or
  `packs/domains/`
- define a manifest contract for:
  - name
  - version
  - default profiles
  - retrieval defaults
  - policy defaults
  - example prompts or templates

Done when:
- one minimal pack loads successfully
- pack metadata is machine-readable
- the core does not need pack-specific `if/else` branches

Suggested initial packs:
- `software`
- `research`
- `support`

### P3-A2 Import/Export

Scope:
- export:
  - `prometheus.toml`
  - `.env`-relevant metadata without secrets
  - trace/failure/outcome store contents
  - lightweight manifest of indexed contexts
- import the same data into a fresh engine root

Done when:
- a user can move a working setup between machines
- export artifacts are deterministic and versioned
- secrets are never included

### P3-A3 Plugin And Tool Registry

Scope:
- define registry contracts for:
  - local plugins
  - tool descriptors
  - optional capability tags
- support discovery without hard-coding every integration in the core

Done when:
- registry entries can be listed and validated
- tools/plugins can declare which contexts or packs they fit
- the core remains agent-agnostic

### P3-A4 Benchmark Harness

Scope:
- fixed retrieval benchmark
- fixed compression fallback benchmark
- fixed setup-mode sanity benchmark
- baseline regression reporting

Done when:
- changes to retrieval/compression/setup can be scored repeatably
- failures are easy to compare between commits

### P3-A5 Extension Docs

Scope:
- how to create a domain pack
- how to export/import a setup
- how to register a plugin/tool
- how to run the benchmark harness

Done when:
- a third party can extend Prometheus without reading large parts of the code

## Recommended Agent Split

1. Domain pack contract and loader
2. Import/export format and versioning
3. Plugin/tool registry contracts
4. Benchmark harness
5. Documentation and examples

## Guardrails

- keep Prometheus engine-first
- do not re-center docs around Odisseu or Claude
- keep manifests declarative
- avoid hidden magic in pack loading or registry discovery
- require tests for every new contract and file format
