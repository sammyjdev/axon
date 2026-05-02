# Prometheus — Agent Context (canonical)

Este é o arquivo canônico de contexto para qualquer agente (Claude Code, Codex, Copilot) trabalhando neste projeto. Leia antes de executar qualquer ação.

## Visão do projeto

**Prometheus** é um "segundo cérebro" self-hosted do Sammy Junior (Senior Full Stack Engineer, Java + React, João Pessoa, BR). Unifica 4 contextos (projetos pessoais, carreira, conhecimento técnico, trabalho/Avangrid) num único grafo consultável por Claude Code, Copilot e terminal (`pb` CLI).

Hardware alvo: Ryzen 7 5800X3D + RTX 4070 Ti + 32GB (PC) e Mac M1 16GB. O sistema roda idêntico nos dois, apenas alternando o profile Docker (`gpu` vs `cpu`).

## Estado atual

**MVP completo (abril 2026).** Todas as fases 0–7 estão implementadas e mergeadas em `master`. 139 testes green. Infra Docker rodando (Colima + 6 containers). MCP conectado ao Claude Code. CLI `pb` instalado via pipx.

O sistema está em uso operacional — o vault cresce organicamente com o uso diário.

## Entry points para agentes

- **README:** [README.md](README.md) — visão geral do sistema, instalação e uso.
- **Backlog:** [TASKS.md](TASKS.md) — todas as tasks estão `done`. Novas tasks futuras entram aqui.
- **Specs originais:** arquivos `prometheus-*.md` na raiz. Nunca edite estes.

## Decisões fixadas (D1-D5)

Estas decisões estão travadas e substituem ambiguidades das specs originais.

### D1 — Paths canônicos

- Dados (Obsidian vault): `~/vault/`
- Engine + specs + docs de agentes: `/Users/samdev/dev/Prometheus/`
- Env vars: `PROMETHEUS_VAULT=~/vault`, `PROMETHEUS_ENGINE=/Users/samdev/dev/Prometheus`
- Nunca inverter. Nunca duplicar.

### D2 — Modelos Anthropic (LiteLLM config)

| TaskType                      | ID                                       |
| ----------------------------- | ---------------------------------------- |
| TRIVIAL_COMPLETION            | `claude-haiku-4-5-20251001`              |
| CODE_ANALYSIS                 | `claude-sonnet-4-6`                      |
| ARCHITECTURE / DEEP_REASONING | `claude-opus-4-7` (com budget explícito) |
| Fallback                      | `claude-haiku-4-5-20251001`              |

Ignorar os nomes `claude-*-4-5` das specs originais — estão desatualizados.

### D3 — Modelos Ollama

- `phi3:mini` — **caveman compressor** (pipeline de compressão semântica). Configurado via `PROMETHEUS_CAVEMAN_MODEL`. Padrão no M1 16GB por ser leve (2.2 GB).
- `gemma4:e4b` — classificador / TIL promoter / scoring. Pesado (9.6 GB) — evitar no M1 em carga simultânea.
- `gemma4:26b` — deep suggester (somente no PC com VRAM suficiente).

No M1 16GB: usar `phi3:mini` para caveman. `gemma4:e4b` só em operações isoladas (sem Colima + VS Code + Chrome simultâneos).

### D4 — Backend de grafo

- **Redis** — grafo de dependências de código (`dep:<symbol>` → calls/called_by). Entra na Fase 3.
- **Neo4j** — exclusivamente para relações do Mem0. Entra na Fase 7.
- Não tentar unificar.

### D5 — Chunker Java (investimento pesado upfront)

O chunker é o componente com maior risco técnico. Antes de qualquer outra coisa da Fase 3:

1. Montar `tests/embedder/fixtures/spring/` com 30+ arquivos reais cobrindo inner classes, anonymous classes, records, generics com bounds, lambdas em Stream pipelines, `@Transactional` com self-invocation, classes 500+ linhas, interfaces com default methods, enums com métodos, annotations custom.
2. Assertions explícitas: contagem de chunks, boundaries byte-exatos, metadata (symbol/type/linha), sem chunks órfãos.
3. Chunker tem que passar 100% da suite antes de sair para o Qdrant.
4. Python e TypeScript reusam a estrutura com fixtures menores (10 cada).

## Stack técnica

- **Linguagem:** Python 3.11+ (engine), Bash (setup), Markdown (vault).
- **Runtime local:** Colima (macOS) + Docker Compose com Qdrant, Redis, Neo4j, Postgres, Langfuse, Ollama (profiles `gpu`/`cpu`).
- **Libs-chave:** `fastembed`, `qdrant-client`, `redis`, `watchdog`, `tree-sitter-java/python/typescript`, `litellm`, `mcp` (FastMCP), `typer` (CLI), `ollama`, `aiosqlite`.
- **Embedding model:** `BAAI/bge-small-en-v1.5` — 384 dimensões (Apple Silicon M-series).
- **IDs Qdrant:** `uuid.uuid5(uuid.NAMESPACE_URL, key)` — SHA1 hex é rejeitado pelo Qdrant.

## Convenções de código

- Type hints sempre (Python 3.11+).
- `dataclass` > dict.
- Async por padrão em I/O.
- Sem comentários óbvios — código autodocumentado. Comente só "porquê" não-óbvio.
- Testes: Testcontainers para integração, sem mocks de repositório.
- `SessionStore`: chamar `.init()` explicitamente — não implementa `__aenter__`/`__aexit__`.
- `pytest-asyncio` com `asyncio_mode = "auto"` e `asyncio_default_fixture_loop_scope = "function"`.

## Regras de navegação por agente

**MVP completo — todas as tasks estão `done`.** Novos agentes que chegarem devem apenas manter o sistema funcional, corrigir bugs pontuais, ou implementar novas tasks adicionadas ao TASKS.md.

## RPG Agent Party

Quando a tarefa envolver análise, desenvolvimento, TDD, review, build, commit ou deploy, leia `docs/AGENT_PARTY.md` e siga o fluxo de classes RPG pragmático do Prometheus.

Regra central: toda mudança de código produtivo começa com estratégia de teste automatizado. Bugfix começa por teste de regressão; feature começa por critérios de aceite testáveis; refactor começa por cobertura ou characterization tests. Testar só depois da implementação é exceção e exige justificativa no handoff.

O playbook não substitui D1-D5, `TASKS.md`, a barreira `work` nem as proibições universais. Em conflito, este `CLAUDE.md` vence.

### Claude Code

Responsável por: qualquer integração multi-módulo, qualquer coisa que toque a barreira `work/`, lógica de contexto e router.

Protocolo:

1. Ler `CLAUDE.md` + `TASKS.md`.
2. Se não houver task `status: open`, só atue se o usuário pedir explicitamente.
3. Implementar com TDD. Nunca silenciar falha de teste.

### Copilot Agent Mode

Protocolo:

1. Ler `CLAUDE.md` + `TASKS.md`.
2. Se não houver task `status: open`, só atue se o usuário pedir explicitamente.
3. Nunca inventar import/lib não listada no `pyproject.toml`.
4. Nunca tocar collections `work` ou `.ctxguard` sem ctx explícito.

## Proibições universais (qualquer agente)

1. **Nunca editar** as 6 specs originais (`/Users/samdev/dev/Prometheus/prometheus-*.md`). São fonte imutável.
2. **Nunca misturar** dados do vault (`~/vault/`) com código do engine (`/Users/samdev/dev/Prometheus/`).
3. **Nunca acessar** `~/vault/work/` ou collections `work` do Qdrant sem `ctx=work` explícito. Barreira protege propriedade intelectual da Avangrid.
4. **Nunca escrever** código proprietário da Avangrid em qualquer lugar do vault ou do engine.
5. **Nunca ignorar** falha de hook ou teste — investigar causa raiz, não silenciar.
6. **Nunca fazer commit** com credenciais, tokens, `.env` ou dados de cliente.

## Workflow de branches

Todas as fases foram mergeadas em `master`. Novas features entram em `feat/<slug>`. Cada PR roda `pytest` + `ruff check` localmente antes do merge.

```
master  ← estado atual (MVP completo)
```

Histórico de commits relevantes:

```
1e0fb81 test(cli): adiciona cobertura para pb ask/search/index/watch
ca23e68 feat(cli): melhora pb ask e amplia watcher para markdown/text
361a887 feat(cli): habilita pb search semântico e pb watch
2598fec feat(cli): habilita pb index com pipeline de ingest e upsert
63ea4cc fix(store): VECTOR_SIZE=384 (bge-small Apple Silicon)
```

## Comandos do agente

```bash
pb ask "dúvida"                # consulta com pipeline caveman+RTK
pb search "símbolo" --ctx work # busca semântica
pb index <path> --ctx work     # indexa código no Qdrant
pb adr add --project <p> --title "..."  # registra decisão
pb til "aprendizado"           # captura TIL
pb cost compression            # tokens economizados pelo pipeline
pb cost today                  # custo LLM do dia
```

Env vars relevantes:
- `PROMETHEUS_CAVEMAN_MODEL` — modelo Ollama do compressor semântico (default: `phi3:mini`)
- `PROMETHEUS_OLLAMA_LOCAL_HOST` — host do Ollama nativo (default: `http://127.0.0.1:11434`)

## Referências rápidas

- README e uso: [README.md](README.md)
- Backlog: [TASKS.md](TASKS.md) — todas `done` (MVP completo)
- Arquitetura 5-layer: [prometheus-context-engine.md](prometheus-context-engine.md)
- Estrutura do vault + barreira work: [prometheus-context-isolation.md](prometheus-context-isolation.md)
- Context detector + cross-platform: [prometheus-context-detection-crossplatform.md](prometheus-context-detection-crossplatform.md)
- TIL→HOW-TO + knowledge split: [prometheus-knowledge-split.md](prometheus-knowledge-split.md)
- Roadmap e stack full: [prometheus-second-brain-full.md](prometheus-second-brain-full.md)
- Vault templates: [prometheus-vault-final.md](prometheus-vault-final.md)

<!-- rtk-instructions v2 -->
# RTK (Rust Token Killer) - Token-Optimized Commands

## Pipeline de Compressão (Prometheus MCP)

O contexto retornado por `pb ask` e pelo MCP `ask` tool passa por duas camadas **complementares** em sequência:

1. **Caveman** (phi3:mini) — semântico: remove filler, mantém assinaturas, regras de domínio e decisões arquiteturais
2. **RTK** — token-level: filtra saída de comandos shell para formato compacto

Ambas sempre rodam. O campo `engine` na resposta confirma quais foram aplicadas:
- `caveman/phi3+rtk` — pipeline completo
- `caveman/phi3` — RTK indisponível (sem binário)
- `fallback` — ambos falharam, contexto bruto

## Golden Rule

**Always prefix commands with `rtk`**. If RTK has a dedicated filter, it uses it. If not, it passes through unchanged. This means RTK is always safe to use.

**Important**: Even in command chains with `&&`, use `rtk`:
```bash
# ❌ Wrong
git add . && git commit -m "msg" && git push

# ✅ Correct
rtk git add . && rtk git commit -m "msg" && rtk git push
```

## RTK Commands by Workflow

### Build & Compile (80-90% savings)
```bash
rtk cargo build         # Cargo build output
rtk cargo check         # Cargo check output
rtk cargo clippy        # Clippy warnings grouped by file (80%)
rtk tsc                 # TypeScript errors grouped by file/code (83%)
rtk lint                # ESLint/Biome violations grouped (84%)
rtk prettier --check    # Files needing format only (70%)
rtk next build          # Next.js build with route metrics (87%)
```

### Test (60-99% savings)
```bash
rtk cargo test          # Cargo test failures only (90%)
rtk go test             # Go test failures only (90%)
rtk jest                # Jest failures only (99.5%)
rtk vitest              # Vitest failures only (99.5%)
rtk playwright test     # Playwright failures only (94%)
rtk pytest              # Python test failures only (90%)
rtk rake test           # Ruby test failures only (90%)
rtk rspec               # RSpec test failures only (60%)
rtk test <cmd>          # Generic test wrapper - failures only
```

### Git (59-80% savings)
```bash
rtk git status          # Compact status
rtk git log             # Compact log (works with all git flags)
rtk git diff            # Compact diff (80%)
rtk git show            # Compact show (80%)
rtk git add             # Ultra-compact confirmations (59%)
rtk git commit          # Ultra-compact confirmations (59%)
rtk git push            # Ultra-compact confirmations
rtk git pull            # Ultra-compact confirmations
rtk git branch          # Compact branch list
rtk git fetch           # Compact fetch
rtk git stash           # Compact stash
rtk git worktree        # Compact worktree
```

Note: Git passthrough works for ALL subcommands, even those not explicitly listed.

### GitHub (26-87% savings)
```bash
rtk gh pr view <num>    # Compact PR view (87%)
rtk gh pr checks        # Compact PR checks (79%)
rtk gh run list         # Compact workflow runs (82%)
rtk gh issue list       # Compact issue list (80%)
rtk gh api              # Compact API responses (26%)
```

### JavaScript/TypeScript Tooling (70-90% savings)
```bash
rtk pnpm list           # Compact dependency tree (70%)
rtk pnpm outdated       # Compact outdated packages (80%)
rtk pnpm install        # Compact install output (90%)
rtk npm run <script>    # Compact npm script output
rtk npx <cmd>           # Compact npx command output
rtk prisma              # Prisma without ASCII art (88%)
```

### Files & Search (60-75% savings)
```bash
rtk ls <path>           # Tree format, compact (65%)
rtk read <file>         # Code reading with filtering (60%)
rtk grep <pattern>      # Search grouped by file (75%)
rtk find <pattern>      # Find grouped by directory (70%)
```

### Analysis & Debug (70-90% savings)
```bash
rtk err <cmd>           # Filter errors only from any command
rtk log <file>          # Deduplicated logs with counts
rtk json <file>         # JSON structure without values
rtk deps                # Dependency overview
rtk env                 # Environment variables compact
rtk summary <cmd>       # Smart summary of command output
rtk diff                # Ultra-compact diffs
```

### Infrastructure (85% savings)
```bash
rtk docker ps           # Compact container list
rtk docker images       # Compact image list
rtk docker logs <c>     # Deduplicated logs
rtk kubectl get         # Compact resource list
rtk kubectl logs        # Deduplicated pod logs
```

### Network (65-70% savings)
```bash
rtk curl <url>          # Compact HTTP responses (70%)
rtk wget <url>          # Compact download output (65%)
```

### Meta Commands
```bash
rtk gain                # View token savings statistics
rtk gain --history      # View command history with savings
rtk discover            # Analyze Claude Code sessions for missed RTK usage
rtk proxy <cmd>         # Run command without filtering (for debugging)
rtk init                # Add RTK instructions to CLAUDE.md
rtk init --global       # Add RTK to ~/.claude/CLAUDE.md
```

## Token Savings Overview

| Category | Commands | Typical Savings |
|----------|----------|-----------------|
| Tests | vitest, playwright, cargo test | 90-99% |
| Build | next, tsc, lint, prettier | 70-87% |
| Git | status, log, diff, add, commit | 59-80% |
| GitHub | gh pr, gh run, gh issue | 26-87% |
| Package Managers | pnpm, npm, npx | 70-90% |
| Files | ls, read, grep, find | 60-75% |
| Infrastructure | docker, kubectl | 85% |
| Network | curl, wget | 65-70% |

Overall average: **60-90% token reduction** on common development operations.
<!-- /rtk-instructions -->
