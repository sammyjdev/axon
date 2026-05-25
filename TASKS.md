# AXON — Backlog de Tasks

Fonte da verdade para atribuição de trabalho entre Claude Code, Codex e Copilot.

## Formato

Cada task segue este schema:

```
## T-NNN: <título>
- phase: <0|0.5|1|...|7>
- agent: claude-code | codex | copilot | manual
- status: open | in-progress | done | blocked
- spec: <arquivo>:<linhas> (referência na spec original)
- branch: feat/phase-N-<slug>
- depends_on: [T-XXX, T-YYY]
- notes: (opcional)
```

## Regras de consumo

1. Agente pega próxima task com `agent:` igual ao seu tipo E `status: open` E `depends_on:` todas `done`.
2. Ao começar, troca `status` para `in-progress`, cria a branch indicada.
3. Ao concluir, troca para `done`, atualiza esta tabela, commita, abre PR.
4. Dois agentes nunca pegam a mesma task. Se `in-progress` já existe, pular para próxima.

---

# Fase 0.5 — Bootstrap de contexto (em curso)

## T-001: Copiar EXECUTION_PLAN.md para projeto

- phase: 0.5
- agent: manual
- status: done
- branch: main

## T-002: Criar CLAUDE.md canônico

- phase: 0.5
- agent: manual
- status: done
- branch: main

## T-003: Criar AGENTS.md symlink para CLAUDE.md

- phase: 0.5
- agent: manual
- status: done
- branch: main

## T-004: Criar .github/copilot-instructions.md

- phase: 0.5
- agent: manual
- status: done
- branch: main

## T-005: Criar TASKS.md (este arquivo)

- phase: 0.5
- agent: manual
- status: done
- branch: main

## T-006: Criar .gitignore e git init

- phase: 0.5
- agent: manual
- status: done
- branch: main
- depends_on: [T-005]

---

# Fase 0 — Fundação do vault (~/vault/)

## T-010: Criar estrutura de pastas do vault

- phase: 0
- agent: manual
- status: done

## T-011: Escrever CLAUDE.md global do vault

- phase: 0
- agent: manual
- status: done
- depends_on: [T-010]

## T-012: Escrever .ctx e .ctxguard por contexto

- phase: 0
- agent: manual
- status: done
- depends_on: [T-010]

## T-013: Escrever CONTEXT.md template + preenchidos

- phase: 0
- agent: manual
- status: done
- depends_on: [T-010]

## T-014: git init + commit inicial do vault

- phase: 0
- agent: manual
- status: done
- depends_on: [T-011, T-012, T-013]

---

# Fase 1 — Infra Docker + bootstrap do engine

## T-020: Criar pyproject.toml + estrutura src/tests/

- phase: 1
- agent: copilot
- model: GPT-4.1
- status: done
- branch: feat/phase-1-docker-infra

## T-021: Escrever docker-compose.yml com profiles gpu/cpu

- phase: 1
- agent: copilot
- model: GPT-5.3-Codex
- status: done
- spec: axon-context-detection-crossplatform.md:324-381
- branch: feat/phase-1-docker-infra
- depends_on: [T-020]

## T-022: Escrever setup.sh cross-platform

- phase: 1
- agent: copilot
- model: GPT-5.3-Codex
- status: done
- spec: axon-context-detection-crossplatform.md:395-433
- branch: feat/phase-1-docker-infra
- depends_on: [T-021]

## T-023: Escrever src/config/platform.py

- phase: 1
- agent: copilot
- model: Gemini 2.5 Pro
- status: done
- spec: axon-context-detection-crossplatform.md:271-318
- branch: feat/phase-1-docker-infra
- depends_on: [T-020]
- notes: lê spec inteira + detecta plataforma — Gemini context window ajuda

## T-024: Subir stack + validar acesso aos serviços

- phase: 1
- agent: manual
- status: done
- notes: Colima como runtime, docker-compose v2.30.3 instalado manualmente, langfuse pinado em :2
- depends_on: [T-022, T-023]

## T-025: Puxar modelos Ollama (gemma4:e4b, phi3:mini, gemma4:26b no PC)

- phase: 1
- agent: manual
- status: done
- notes: phi3:mini ok. Ollama upgradeado 0.18.2→0.21.0 (fix de permissão /opt/homebrew). gemma4:e4b pulled (9.6GB). gemma4:26b pendente (Mac M1 16GB sem VRAM — baixar só no PC)
- depends_on: [T-024]

---

# Fase 2 — Store layer

## T-030: src/store/vector_store.py (Qdrant, collections por ctx)

- phase: 2
- agent: copilot
- model: GPT-5.3-Codex
- status: done
- spec: axon-context-engine.md:114-139
- branch: feat/phase-2-store-layer

## T-031: src/store/graph_store.py (Redis, deps de código)

- phase: 2
- agent: copilot
- model: GPT-5.3-Codex
- status: done
- spec: axon-context-engine.md:143-154
- branch: feat/phase-2-store-layer

## T-032: src/store/session_store.py (SQLite: adr, session_memory, code_change)

- phase: 2
- agent: copilot
- model: GPT-5.3-Codex
- status: done
- spec: axon-context-engine.md:158-187
- branch: feat/phase-2-store-layer

## T-033: src/store/collections.py com barreira work

- phase: 2
- agent: claude-code
- status: done
- spec: axon-context-isolation.md:154-171
- branch: feat/phase-2-store-layer
- notes: barreira é crítica — só Claude Code edita

## T-034: Testes unitários dos stores

- phase: 2
- agent: copilot
- model: GPT-5.2-Codex
- status: done
- branch: feat/phase-2-store-layer
- depends_on: [T-030, T-031, T-032, T-033]

---

# Fase 3a — Chunker Java (TDD crítico, gate bloqueante)

## T-040: Montar tests/embedder/fixtures/spring/ com 30+ arquivos reais

- phase: 3
- agent: claude-code
- status: done
- spec: CLAUDE.md D5
- branch: feat/phase-3a-chunker-java
- notes: catálogo em D5 — inner classes, records, generics, lambdas, self-invocation, etc.

## T-041: Escrever tests/embedder/test_chunker_java.py com assertions por fixture

- phase: 3
- agent: claude-code
- status: done
- branch: feat/phase-3a-chunker-java
- depends_on: [T-040]

## T-042: src/embedder/chunker.py (Java via tree-sitter) — iterar até 100% green

- phase: 3
- agent: claude-code
- status: done
- spec: axon-context-engine.md:94-102
- branch: feat/phase-3a-chunker-java
- depends_on: [T-041]

## T-042b: [PARALELO] src/embedder/chunker.py via Copilot — comparar com T-042

- phase: 3
- agent: copilot
- model: Claude Opus 4.6
- status: done
- spec: axon-context-engine.md:94-102
- branch: feat/phase-3a-chunker-java-copilot
- depends_on: [T-041]
- notes: corre em paralelo com T-042. Ao final comparar diffs; pegar o melhor ou fazer cherry-pick. Merge apenas da versão escolhida.

---

# Fase 3b — Embedder + Watcher

## T-050: Estender chunker para Python e TypeScript

- phase: 3
- agent: claude-code
- status: done
- branch: feat/phase-3b-embedder-watcher
- depends_on: [T-042]

## T-051: src/embedder/engine.py (fastembed + platform-aware providers)

- phase: 3
- agent: copilot
- model: Claude Sonnet 4.6
- status: done
- spec: axon-context-engine.md:83-106
- branch: feat/phase-3b-embedder-watcher
- depends_on: [T-042]

## T-052: src/watcher/main.py (watchdog + fila async)

- phase: 3
- agent: copilot
- model: Gemini 3 Flash
- status: done
- spec: axon-context-engine.md:51-70
- branch: feat/phase-3b-embedder-watcher
- depends_on: [T-051]

## T-053: Pipeline de ingest watcher→chunker→embedder→vector_store

- phase: 3
- agent: copilot
- model: Claude Sonnet 4.6
- status: done
- branch: feat/phase-3b-embedder-watcher
- depends_on: [T-052]

## T-054: Script one-shot de indexação inicial

- phase: 3
- agent: copilot
- model: GPT-5.3-Codex
- status: done
- branch: feat/phase-3b-embedder-watcher
- depends_on: [T-053]

---

# Fase 4 — MCP Gateway

## T-060: src/mcp/server.py (FastMCP + tools base)

- phase: 4
- agent: claude-code
- status: done
- spec: axon-context-engine.md:242-275, axon-context-isolation.md:178-228
- branch: feat/phase-4-mcp-gateway
- notes: tools search_code, get_adrs, save_adr, get_session_memory, get_dependencies

## T-061: Aplicar CONTEXT_BUDGETS (claude-code 8k, copilot 2k)

- phase: 4
- agent: claude-code
- status: done
- spec: axon-context-engine.md:279-284
- branch: feat/phase-4-mcp-gateway
- depends_on: [T-060]

## T-062: Registrar MCP no Claude Code + smoke test

- phase: 4
- agent: manual
- status: done
- notes: smoke test ok (infra ok, coleções Qdrant serão criadas na primeira indexação via pb index)
- depends_on: [T-061]

---

# Fase 5a — Context Detector + Router

## T-070: src/context/detector.py (scoring cwd/content/session)

- phase: 5
- agent: claude-code
- status: done
- spec: axon-context-detection-crossplatform.md:32-163
- branch: feat/phase-5a-detector-router

## T-071: src/router/classifier.py (phi3:mini local)

- phase: 5
- agent: claude-code
- status: done
- spec: axon-context-engine.md:202-207
- branch: feat/phase-5a-detector-router

## T-072: src/router/engine.py (LiteLLM + budget gate + Langfuse)

- phase: 5
- agent: claude-code
- status: done
- spec: axon-context-engine.md:209-231 (+ D2 para IDs atuais)
- branch: feat/phase-5a-detector-router
- depends_on: [T-071]

## T-073: Tool MCP ask(query, cwd, ctx?) integrando detector+router

- phase: 5
- agent: claude-code
- status: done
- spec: axon-context-detection-crossplatform.md:171-207
- branch: feat/phase-5a-detector-router
- depends_on: [T-070, T-072]

---

# Fase 5b — CLI pb

## T-080: src/cli/pb.py (typer, comandos base)

- phase: 5
- agent: copilot
- model: GPT-5.3-Codex
- status: done
- spec: axon-context-isolation.md:386-412, axon-vault-final.md:386-402
- branch: feat/phase-5b-cli-pb
- notes: pb ask, search, adr, session, cost

## T-081: Instalar pb como entry_point (pipx)

- phase: 5
- agent: manual
- status: done
- notes: pipx install --editable . com Python 3.11 (requires-python baixado para >=3.11)
- depends_on: [T-080]

---

# Fase 6 — Knowledge automation

## T-090: src/vault/til_promoter.py

- phase: 6
- agent: copilot
- model: GPT-5.3-Codex
- status: done
- spec: axon-vault-final.md:237-352
- branch: feat/phase-6-knowledge-automation

## T-091: Git hook post-commit no vault

- phase: 6
- agent: copilot
- model: Gemini 3 Flash
- status: done
- spec: axon-vault-final.md:358-379
- branch: feat/phase-6-knowledge-automation
- depends_on: [T-090]

## T-092: Comandos CLI pb til/howto/til --promote-today

- phase: 6
- agent: copilot
- model: GPT-5.3-Codex
- status: done
- spec: axon-vault-final.md:386-402
- branch: feat/phase-6-knowledge-automation
- depends_on: [T-080]

## T-093: src/vault/deep_suggester.py

- phase: 6
- agent: copilot
- model: Gemini 2.5 Pro
- status: done
- spec: axon-knowledge-split.md:128-188
- branch: feat/phase-6-knowledge-automation
- notes: precisa ler muitas notas de uma vez — janela 1M do Gemini ideal

## T-094: Comandos CLI pb deep --suggest / --list

- phase: 6
- agent: copilot
- model: GPT-5.3-Codex
- status: done
- branch: feat/phase-6-knowledge-automation
- depends_on: [T-093]

---

# Fase 7 — Mem0 + memória de sessão

## T-100: Configurar Mem0 self-hosted (Qdrant + Neo4j)

- phase: 7
- agent: claude-code
- status: done
- spec: axon-context-isolation.md:256-287
- branch: feat/phase-7-mem0-compressor

## T-101: Tool MCP get_memory(query, ctx) com filtro work

- phase: 7
- agent: claude-code
- status: done
- spec: axon-context-isolation.md:201-211
- branch: feat/phase-7-mem0-compressor
- depends_on: [T-100]

## T-102: Session memory compressor (Haiku a cada 10 turns)

- phase: 7
- agent: claude-code
- status: done
- spec: axon-context-engine.md:303-314
- branch: feat/phase-7-mem0-compressor

## T-103: Hook fim-de-sessão → resumo no daily note

- phase: 7
- agent: claude-code
- status: done
- branch: feat/phase-7-mem0-compressor
- depends_on: [T-102]

## T-104: [BUG] CompressionRecord polluted by graph tool I/O

- phase: 7
- agent: claude-code
- status: open
- branch: fix/compression-telemetry-pollution
- notes:
  `src/axon/mcp/server.py:99-122` reuses `CompressionRecord` to log I/O from
  `get_graph_path` and `get_graph_neighbors`. These are not compression events.

  **Impact**: `data/compression/stats.jsonl` mixes two populations. Aggregation
  without engine filtering produces misleading averages (2% vs. 55% real). Of
  197 records in production, 119 are graph-tool I/O, not compression.

  **Fix options**:
  - Separate stream: `data/observability/tool_io.jsonl` with its own schema.
  - Or add `kind: "compression" | "tool_io"` field to `CompressionRecord`
    and filter on read.

  **Workaround until fixed**: aggregators must filter
  `engine IN ("caveman/phi3+rtk", "caveman/phi3", "rtk", "fallback", "disabled")`
  before computing any statistic.

## T-105: [PERF] Caveman compression dominates pb ask latency with 100% reject rate

- phase: 7
- agent: claude-code
- status: open
- branch: fix/caveman-quality-gate-or-skip
- depends_on: [T-104]
- notes:
  Stage-by-stage breakdown of `pb ask` (3 in-process iterations, query
  "what is the hexagonal architecture decision for this project", ctx=knowledge,
  ~1.4K tokens of retrieved context):

  ```
  stage                   iter1 (cold)  iter2 (warm)  iter3 (warm)
  context_detection             0.001s        0.000s        0.000s
  strategy_select               3.306s        0.000s        0.000s
  retrieval                     1.051s        0.308s        0.310s
  context_pack_build            0.000s        0.000s        0.000s
  compression                   6.469s       10.978s        6.787s   ← always dominant
  prompt_build                  0.000s        0.000s        0.000s
  _wall                        10.827s       11.287s        7.098s
  ```

  **Observed behavior**: every iteration prints
  `caveman_note: compression output rejected: missing source symbol(s): …`
  and `tokens aprox: N -> N (-0.0%)`. Caveman calls `phi3:mini` via Ollama
  (6-11s), output is rejected by `compression_quality_note` (preservation
  check), system falls back to RTK with ~0% net reduction.

  This is consistent with production telemetry: 7 of 78 real compression
  attempts in `stats.jsonl` produced any reduction. The other 71 paid the
  caveman cost for no gain.

  **Likely fixes** (not yet investigated):
  - Tighten the input gate so caveman only runs when symbol-preservation
    is feasible (e.g., skip when the input is mostly file-list snippets,
    as in the retrieved-context case).
  - Relax `compression_quality_note` symbol-preservation rule for
    retrieval-context inputs (where symbols are file paths, not code).
  - Make caveman opt-in per strategy instead of default-on.

  **Article impact**: Documented in T6 launch post — "Retrieval pipeline
  warm path: p50 = 0.3s. End-to-end `pb ask` includes a compression step
  under active investigation (this issue) that adds 6-11s per call due to
  a quality-gate rejection issue."

  **Reproduction**: `python3 -m axon.cli.pb ask "<query>"` with Ollama up
  and `gemma4:e4b`/`phi3:mini` loaded. Inspect the `compression:` block in
  the stdout — note the `caveman_note: compression output rejected` and
  `(-0.0%)` token reduction.

---

# Quadro resumo por agente

| Agente      | Open                                     | In-progress | Done                                                                         |
| ----------- | ---------------------------------------- | ----------- | ---------------------------------------------------------------------------- |
| claude-code | —                                        | —           | T-033, T-040..T-042, T-050, T-060, T-061, T-070..T-073, T-100..T-103         |
| copilot     | —                                        | —           | T-020..T-023, T-030..T-032, T-034, T-042b, T-051..T-054, T-080, T-090..T-094 |
| manual      | T-010..T-014, T-024, T-025, T-062, T-081 | —           | T-001..T-006                                                                 |

> **Consolidação:** todas as branches mergeadas em `master`. 118 testes green (chunker), 17 testes green (stores).
> Tasks abertas restantes são exclusivamente manuais (vault setup, registro MCP, pipx).

## Roteamento de modelo por task (Copilot)

| Task                      | Modelo            | Motivo                             |
| ------------------------- | ----------------- | ---------------------------------- |
| T-020 pyproject.toml      | GPT-4.1 (0x)      | Config trivial, grátis             |
| T-021 docker-compose      | GPT-5.3-Codex     | Especializado em YAML/infra        |
| T-022 setup.sh            | GPT-5.3-Codex     | Script bash estruturado            |
| T-023 platform.py         | Gemini 2.5 Pro    | Lê spec inteira, detecta hardware  |
| T-030..032 stores         | GPT-5.3-Codex     | CRUD Python, boilerplate           |
| T-034 testes stores       | GPT-5.2-Codex     | Boilerplate de testes              |
| T-042b chunker (paralelo) | Claude Opus 4.6   | Qualidade máxima para gate crítico |
| T-051 embedder engine     | Claude Sonnet 4.6 | Lógica de providers dinâmicos      |
| T-052 watcher             | Gemini 3 Flash    | Watchdog simples, rápido           |
| T-053 pipeline ingest     | Claude Sonnet 4.6 | Integração multi-módulo            |
| T-054 indexação one-shot  | GPT-5.3-Codex     | Script sequencial simples          |
| T-080 CLI pb              | GPT-5.3-Codex     | Typer scaffolding                  |
| T-090 til_promoter        | GPT-5.3-Codex     | Tradução direta da spec            |
| T-091 git hook            | Gemini 3 Flash    | Bash hook simples                  |
| T-092 pb til/howto CLI    | GPT-5.3-Codex     | Extensão do CLI                    |
| T-093 deep_suggester      | Gemini 2.5 Pro    | Lê muitas notas de uma vez         |
| T-094 pb deep CLI         | GPT-5.3-Codex     | Extensão do CLI                    |
