# Prometheus Support Matrix

Status: active distribution guidance

Prometheus stays agent-agnostic. This matrix describes the supported ways to
run the engine and its surrounding services on real developer machines. It is
product guidance, not a promise that every local model stack or GPU runtime is
equally mature on every host.

## Support Principles

- Prefer the smallest operating mode that reliably reaches first indexing and
  first query.
- Treat GPU acceleration as optional optimization, not as a requirement for
  basic Prometheus use.
- When a machine is undersized or driver setup is unstable, move down to
  `minimal` or move heavy services to `remote-infra` instead of forcing
  `full-local`.

## Status Meanings

| Status | Meaning |
| --- | --- |
| recommended | best default for new users and product-facing docs |
| supported | expected to work, but not the first path we should optimize for |
| limited | usable for narrower workflows only; expect sharper caveats |

## Validation Paths

Use one of these acceptance paths when validating an environment.

### Minimal path

```bash
pb --help
pb index ~/vault/knowledge --ctx knowledge
pb ask "health check"
```

### Infra path

Use this when the chosen mode depends on Docker-managed services or memory
features.

```bash
docker compose ps
pb search "health check" --ctx knowledge --top 1
pb memory smoke --ctx knowledge
```

## Platform Matrix

| Platform | Status | Recommended mode | Caveats | Validation path |
| --- | --- | --- | --- | --- |
| macOS | supported | `hybrid-local` | Good developer experience for the engine itself, but RAM pressure appears quickly when Docker and local models share the same laptop. Start with `hybrid-local`; use `remote-infra` for heavier local models or always-on services. `full-local` is reasonable only on stronger machines. | minimal path first; add infra path only if enabling Docker-backed services locally |
| Linux | recommended | `full-local` or `remote-infra` | Best target for long-running local services and the cleanest path for heavier self-hosted setups. Distro packaging still varies, so support should focus on mainstream developer distributions instead of every Linux variant. | infra path for `full-local`; minimal path for `minimal`; either path works for `remote-infra` depending on what stays local |
| Windows with WSL2 | supported | `minimal` or `remote-infra` | This is the Windows support path. Run Prometheus inside WSL2 and keep engine, repo, and vault paths consistent there. Avoid a split setup where commands run in WSL2 but data or services are managed ad hoc from native Windows. `full-local` is possible but should not be the default recommendation. | minimal path from a WSL2 shell; add infra path only after local service wiring is stable |

## Accelerator Matrix

| Hardware profile | Status | Recommended mode | Caveats | Validation path |
| --- | --- | --- | --- | --- |
| CPU-only | recommended | `minimal` or `remote-infra` | Baseline support target. This is the safest default for onboarding, CI-style validation, and lower-resource laptops. Expect slower local generation or compression workloads if you insist on keeping model execution on the same machine. | minimal path |
| AMD GPU | supported | `remote-infra` or `hybrid-local` | Treat AMD acceleration as optional. Keep a CPU fallback path working first, then enable GPU-backed local model serving only if the host stack is already stable. Avoid making AMD-specific runtime setup a prerequisite for basic Prometheus adoption. | minimal path first; then infra path if the AMD-backed service host is part of the chosen mode |
| NVIDIA GPU | recommended | `full-local` or `remote-infra` | Best fit for heavier local-model workloads. Even here, separate "Prometheus works" from "GPU acceleration is tuned." The engine should validate on the minimal path before CUDA-specific optimization or larger local models become part of the support claim. | minimal path first; infra path for the final `full-local` or GPU-backed `remote-infra` shape |

## Recommended Starting Points

| Situation | Start here | Why |
| --- | --- | --- |
| New user on a laptop | `minimal` | Fastest path to first success, lowest setup risk |
| macOS developer who wants local UX without running everything locally | `hybrid-local` | Keeps the engine local while avoiding the worst laptop resource contention |
| Linux workstation with enough RAM and interest in self-hosting | `full-local` | Most complete local experience and strongest long-running host story |
| Team member on Windows | `remote-infra` through WSL2 | Simplest way to avoid host-boundary issues while keeping the CLI local |
| Any machine with unstable GPU drivers or undersized hardware | `minimal` or `remote-infra` | Better product experience than a fragile `full-local` install |

## Distribution Guidance

- Document Windows support as `WSL2`, not native Windows.
- Keep CPU-only onboarding as a first-class path in quickstarts and validation.
- Position NVIDIA as the best heavy local path, not as a requirement.
- Position AMD as viable where the local model stack is already proven, not as
  the default setup target.
- Use `remote-infra` as the pressure-release valve whenever local hardware,
  drivers, or laptop thermals turn `full-local` into operational overhead.
