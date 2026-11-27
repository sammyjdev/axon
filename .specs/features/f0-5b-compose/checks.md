# F0.5b compose checks

Build: `ruff check src`; `python3 -m compileall -q src`
Run tests: `python3 -m pytest tests/config/test_compose_file.py tests/config/test_setup_script.py tests/test_database_survives_reboot.py -v -rs`
Plan: `.specs/features/f0-5b-compose/plan.md`

13 checks in 3 slices · 1 one-way door · 0 open, of which 0 block

C<n> proves acceptance criterion <n> of the plan. The compose config tests need Docker Compose v2:
they run in CI (`CI` set, no skip) and skip on a host without it.

## Checks

### S1 - compose holds only Postgres, unchanged

**C1** - docker-compose.yml holds 0 case-insensitive occurrences of `ollama`, and `docker compose -f docker-compose.yml --profile cpu --profile gpu config --services` exits 0 and prints exactly `axon-postgres` (AC 1)
Proof: run tests | test_only_postgres_is_defined PASSED
Proof: run tests | test_compose_config_lists_only_postgres PASSED
Red at base: both fail (24 occurrences; three services listed, Docker runner).

**C2** - `axon-postgres` keeps image `pgvector/pgvector:pg16`, port `5433:5432`, volume `./data/axon-postgres:/var/lib/postgresql/data`, the three `POSTGRES_*` values and `restart: unless-stopped`, with the door-1 comment on the line above `image:` (AC 2)
Proof: run tests | test_postgres_service_is_unchanged PASSED
Proof: run tests | test_compose_config_keeps_postgres_unchanged PASSED
Proof: run tests | test_database_survives_reboot.py PASSED
Red at base: the static test fails on the missing comment. The JSON test passes at base (guard); deliberate break: publish `5434:5432` and it turns red.

**C13** - With `CI` set and no runnable `docker compose`, the config helper raises `AssertionError` mentioning `docker compose`; only the two Docker tests carry the `skipif`, and the two static tests carry no skip marker (AC 13)
Proof: run tests | test_ci_never_skips_compose PASSED
Red at base: the test file does not exist. Deliberate break: remove the assert in the helper and the test turns red.

### S2 - setup starts only Postgres, pulls nothing, picks its mode without Ollama

**C3** - In `hybrid-local` (macOS faked) and `full-local` (Linux) the command log holds `docker compose up -d axon-postgres` and `docker compose exec -T axon-postgres pg_isready`, 0 `--profile`, and stdout holds `==> Subindo Postgres (docker compose up -d axon-postgres)` (AC 3)
Proof: run tests | test_setup_defaults_to_recommended_hybrid_local_mode_on_mac PASSED
Proof: run tests | test_setup_full_local_without_nvidia_starts_only_postgres PASSED
Red at base: the log holds `docker compose --profile cpu up -d`.

**C4** - 0 `ollama` commands run in `hybrid-local`, `full-local`, `remote-infra` and `minimal`, and the plan's `grep -c` over setup.sh prints `0` (AC 4)
Proof: run tests | test_setup_defaults_to_recommended_hybrid_local_mode_on_mac PASSED
Proof: run tests | test_setup_full_local_without_nvidia_starts_only_postgres PASSED
Proof: run tests | test_setup_uses_remote_infra_when_host_is_configured PASSED
Proof: run tests | test_setup_honors_minimal_mode_and_skips_local_bootstrap PASSED
Proof: run tests | test_setup_has_no_profile_or_model_residue PASSED
Red at base: `ollama pull` is logged; the grep prints `18`.

**C5** - `remote-infra` with `AXON_INFRA_HOST=desktop.local` prints `Postgres` and logs 0 occurrences of `11434` (AC 5)
Proof: run tests | test_setup_uses_remote_infra_when_host_is_configured PASSED
Red at base: the curl probe of port 11434 is logged.

**C6** - After a `hybrid-local` or `full-local` run the workspace holds no `data/ollama` directory (AC 6)
Proof: run tests | test_setup_defaults_to_recommended_hybrid_local_mode_on_mac PASSED
Proof: run tests | test_setup_full_local_without_nvidia_starts_only_postgres PASSED
Red at base: `mkdir -p data/ollama` creates it.

**C7** - Every finishing mode prints `Optional TIL hook: install Ollama (https://ollama.com) and run: ollama pull gemma4:e4b` (AC 7)
Proof: run tests | test_setup_defaults_to_recommended_hybrid_local_mode_on_mac PASSED
Proof: run tests | test_setup_full_local_without_nvidia_starts_only_postgres PASSED
Proof: run tests | test_setup_uses_remote_infra_when_host_is_configured PASSED
Proof: run tests | test_setup_honors_minimal_mode_and_skips_local_bootstrap PASSED
Red at base: the line is absent.

**C8** - When `docker compose up -d axon-postgres` exits 1, setup.sh exits non-zero and does not print `Setup concluído` (AC 8)
Proof: run tests | test_setup_fails_when_compose_fails PASSED
Red at base: none, it passes through `set -e` (guard). Deliberate break: `docker compose up -d axon-postgres || true` turns it red.

**C10** - With no mode configured and no `ollama`, Linux with Docker gets `full-local` for a fake `nvidia-smi` of `24576` MB and `hybrid-local` for a failing `nvidia-smi` (AC 10)
Proof: run tests | test_default_mode_ignores_ollama[24576-full-local] PASSED
Proof: run tests | test_default_mode_ignores_ollama[None-hybrid-local] PASSED
Red at base: the 24576 case gives `hybrid-local`.

**C11** - With no mode configured, no `docker` and an `ollama` on `PATH`, setup picks `minimal`, exits 0 and runs 0 `docker` commands (AC 11)
Proof: run tests | test_no_docker_with_ollama_is_minimal PASSED
Red at base: `hybrid-local` is picked and the run fails on `docker`.

**C12** - A new `.env.local` from `AXON_INFRA_HOST=desktop.local` keeps that line and holds no generated Ollama or Langfuse value; a seeded `AXON_OLLAMA_REMOTE_HOST=http://old:11434` is kept unchanged and the run without a remote host picks `hybrid-local` and exits 0 (AC 12)
Proof: run tests | test_env_local_has_no_generated_ollama_keys PASSED
Proof: run tests | test_existing_env_local_keys_are_kept PASSED
Red at base: generated keys are written; the seeded key forces `remote-infra` and exit 1.

### S3 - docs match

**C9** - The three quickstarts hold 0 of the five old phrases, Linux and macOS name `remove-orphans` once each, WSL2 0 times, and the Linux intro reads `engine and stores on the same machine.` (AC 9)
Proof: `for f in docs/QUICKSTART_LINUX.md docs/QUICKSTART_MACOS.md docs/QUICKSTART_WINDOWS_WSL2.md; do test "$(grep -c -e '--profile' -e 'pulls the default Ollama' -e 'CPU profile' -e 'probes Ollama' -e 'local models on the same machine' $f)" = 0 || exit 1; done; test "$(grep -c remove-orphans docs/QUICKSTART_LINUX.md)" = 1 && test "$(grep -c remove-orphans docs/QUICKSTART_MACOS.md)" = 1 && test "$(grep -c remove-orphans docs/QUICKSTART_WINDOWS_WSL2.md)" = 0 && tr '\n' ' ' < docs/QUICKSTART_LINUX.md | grep -q 'Linux is the strongest path for a full local AXON install: engine and stores on the same machine\.'`
Red at base: the first grep prints 2, 1 and 1.

## Coverage

| Set (size) | Member -> proof | Unproven |
| --- | --- | --- |
| acceptance criteria (13) | AC1 C1 · AC2 C2 · AC3 C3 · AC4 C4 · AC5 C5 · AC6 C6 · AC7 C7 · AC8 C8 · AC9 C9 · AC10 C10 · AC11 C11 · AC12 C12 · AC13 C13 | - |
| setup modes (4) | `hybrid-local` C3 · `full-local` C3 · `remote-infra` C5 · `minimal` C4 | - |
| default-mode rule branches (4) | remote host C12 · NVIDIA `full-local` C10 · Docker `hybrid-local` C10 · no Docker `minimal` C11 | - |
| compose tests under the skip rule (4) | `lists_only_postgres` C13 · `keeps_postgres_unchanged` C13 · `only_postgres_is_defined` C13 · `postgres_service_is_unchanged` C13 | - |
| `.env.local` cases (2) | fresh file C12 · existing key C12 | - |
| quickstarts (3) | Linux C9 · macOS C9 · WSL2 C9 | - |
| one-way doors (1) | door 1 image comment C2 | - |

## Swept

- validation: n/a - no new input; the mode and host values keep today's handling
- failure modes: C8
- idempotency: C12
- authorization: n/a - a local bootstrap script with no callers or roles
- concurrency: n/a - one sequential script run by one user
- data lifecycle: C2, C12
- dependency failure: C8, C11, C13
- state transitions: C10, C11, C12
- observability: C3, C7

## Handoff

- **Settled mid-build:** none
- **Dispatched:** none - review and acceptance are dispatched by Otto
- **Boundary:** C1-C13 closed at `a71958d`; red on CI with the base production files at `f4f927d` (temporary commit `1c20241`, reverted)
- **Abandoned:** an isolated test `PATH` without `mkdir` made base runs die early; `mkdir` was added
