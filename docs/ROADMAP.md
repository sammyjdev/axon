# Prometheus Roadmap

Status: active execution plan

This roadmap turns the current product direction into concrete tasks. The near
term goal is simple: make Prometheus usable by other developers on different
machines without assuming Odisseu, Claude, or the author's hardware.

## Product Direction

- Prometheus: self-hosted context, memory, retrieval, and governance engine.
- Odisseu: advanced autonomous consumer, not the center of the engine.
- Integration between them: explicit adapter boundary, not product identity.

## Operating Modes

- `full-local`: all infra and local models on the same machine.
- `hybrid-local`: local engine plus reduced local infra and optional cloud
  fallback.
- `remote-infra`: Prometheus local, heavy services on another machine.
- `minimal`: smallest supported stack for low-resource laptops and first-time
  setup.

## Exit Criteria For This Phase

- A new developer can choose a mode, run setup, and complete first indexing and
  first query without manual author intervention.
- Prometheus can explain when a machine is undersized and recommend a safer
  mode.
- Prometheus can hide advanced components when they are overkill for the
  user's problem.

## P0: Distribution Foundation

| ID | Task | Deliverable | Depends on | Done when |
| --- | --- | --- | --- | --- |
| P0-T1 | Define support matrix | `docs/SUPPORT_MATRIX.md` covering macOS, Linux, Windows/WSL2, CPU, AMD, NVIDIA | none | each OS/mode combination has status, caveats, and recommended path |
| P0-T2 | Define runtime mode schema | `prometheus.toml` schema for `full-local`, `hybrid-local`, `remote-infra`, `minimal` | P0-T1 | config loader can validate mode and fail with clear errors |
| P0-T3 | Add `pb init` | guided bootstrap command for engine path, vault path, mode, and profile | P0-T2 | fresh machine can generate local config and env scaffold |
| P0-T4 | Add `pb doctor` | environment and hardware probe for Docker, Python, Ollama, RAM, GPU, service reachability | P0-T2 | command emits pass/warn/fail plus recommended operating mode |
| P0-T5 | Split setup paths by mode | setup flow for local-only, remote-infra, and minimal installs | P0-T2 | setup no longer assumes one infra shape |
| P0-T6 | Publish OS quickstarts | setup guides for macOS, Linux, Windows/WSL2 | P0-T3 | each guide reaches `pb ask` happy path |
| P0-T7 | Add `.env.example` and minimal compose defaults | repo templates safe for external users | P0-T3 | new user can inspect expected vars without reading source |

## P1: Guided Customization

| ID | Task | Deliverable | Depends on | Done when |
| --- | --- | --- | --- | --- |
| P1-T1 | Define profile manifest | profile schema for use cases such as `solo-dev`, `team-dev`, `privacy-first`, `low-resource` | P0-T2 | profiles can express enabled services, policies, and feature flags |
| P1-T2 | Build user-needs questionnaire | prompts mapping problem type, privacy needs, and hardware to a profile | P1-T1 | answers produce deterministic profile recommendations |
| P1-T3 | Add `pb configure` | interactive profile/customization command | P1-T2 | user can reconfigure without editing raw files |
| P1-T4 | Build capability selector | rules deciding which subsystems are necessary vs overkill | P1-T1 | low-resource users avoid heavy components by default |
| P1-T5 | Add profile docs and examples | examples for common developer setups | P1-T3 | users can compare profiles before installing |

## P2: Engine Robustness

| ID | Task | Deliverable | Depends on | Done when |
| --- | --- | --- | --- | --- |
| P2-T1 | Define `ContextPack` contract | structured context payload format | P1-T1 | CLI/MCP can return structured context, not only free text |
| P2-T2 | Define `RetrievalStrategy` contract | strategy selection by task type and profile | P2-T1 | retrieval can vary by use case without branching ad hoc code |
| P2-T3 | Add `TraceStore` | retrieval-to-output correlation records | P2-T1 | one request can be traced across retrieval, compression, and policy |
| P2-T4 | Add `FailureStore` | persistent failure records with probable cause and tags | P2-T3 | repeated failures become queryable history |
| P2-T5 | Add `OutcomeStore` | persistent record of successful outcomes by context | P2-T3 | successful patterns become reusable memory |
| P2-T6 | Add compression confidence | score plus fallback policy | P2-T1 | critical flows can keep full context when confidence is low |
| P2-T7 | Add staleness detection | outdated-memory tagging and replacement heuristics | P2-T4 | retrieval can down-rank obsolete context |

## P3: Extensibility

| ID | Task | Deliverable | Depends on | Done when |
| --- | --- | --- | --- | --- |
| P3-T1 | Add domain-pack layout | installable packs for software, research, support, and corporate use | P1-T1 | packs can add retrieval defaults, policies, and examples |
| P3-T2 | Add import/export | portable backup for vault metadata, local stores, and config | P0-T2 | users can migrate or share setups predictably |
| P3-T3 | Add plugin/tool registry | extension point for tools and future integrations | P2-T2 | third parties can extend behavior without editing core |
| P3-T4 | Add benchmark suite | fixed checks for retrieval quality, setup cost, and compression safety | P2-T6 | regressions are measurable before release |

## Not In Scope For This Phase

- making Prometheus itself the deep-agent runtime
- coupling core workflows to Claude-specific hooks
- requiring Odisseu for setup, planning, or retrieval
- shipping every advanced subsystem to every user by default
