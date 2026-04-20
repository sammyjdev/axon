# Prometheus — Plano de Execução Passo a Passo

## Contexto

Os 6 arquivos em `/Users/samdev/dev/Prometheus/` descrevem **Prometheus**: um "segundo cérebro" self-hosted que unifica 4 contextos do Sammy (projetos pessoais, carreira, conhecimento técnico, trabalho/Avangrid) num único grafo consultável por Claude Code, Copilot e terminal. Hoje o diretório tem **apenas as specs em markdown** — nenhum código, nenhum vault, nenhum compose. Este plano transforma as specs num sistema executável, começando pelo MVP de 2 semanas descrito em [prometheus-second-brain-full.md](/Users/samdev/dev/Prometheus/prometheus-second-brain-full.md) e avançando até as fases posteriores.

**Objetivo do MVP (fim de 2 semanas):** Claude Code sabe o que foi feito ontem, por que a decisão X foi tomada, e consegue buscar no codebase. Terminal tem `pb` como acesso único. Custo alvo: ~$0.03/dia em tokens.

**Repositórios alvo:**
- `~/vault/` — dados (Obsidian, markdown, git-tracked)
- `~/prometheus/` — engine Python (MCP server, watcher, indexer, router, CLI)

---

## Fase 0.5 — Disponibilizar plano + criar arquivos base de cada agente (imediato, <15min)

**Objetivo:** garantir que nem Claude Code, nem Codex, nem Copilot "se percam" do contexto ao abrir o projeto. Todos devem convergir para a mesma fonte da verdade antes de qualquer implementação.

Ações em `/Users/samdev/dev/Prometheus/`:

1. **Copiar este plano para o projeto** — `EXECUTION_PLAN.md` na raiz. Assim qualquer agente que abrir o diretório vê o plano imediatamente (o plano atual vive em `~/.claude/plans/` e não é portável).
2. **Criar `CLAUDE.md`** — contexto canônico para Claude Code. Contém: visão do projeto, estado atual (greenfield), D1-D5 decisões fixadas, paths canônicos, instruções de navegação (ler EXECUTION_PLAN.md + TASKS.md antes de agir), regras de escrita (nunca tocar specs, nunca misturar vault/engine).
3. **Criar `AGENTS.md`** — mesmo conteúdo do CLAUDE.md (Codex CLI lê este arquivo nativamente). Implementar como **symlink** `AGENTS.md -> CLAUDE.md` para garantir que nunca dessincronizem.
4. **Criar `.github/copilot-instructions.md`** — versão condensada (~40 linhas): stack Python 3.12, D1-D5, convenções de código (sem comentários óbvios, type hints sempre, records Java, testcontainers para integração), proibições (não inventar imports, não editar specs, não editar CLAUDE.md).
5. **Criar `TASKS.md`** — backlog inicial com as tasks das Fases 0-7 já tagueadas com `agent:` e `status: open`. Formato por entrada:
   ```
   ## T-003: docker-compose + setup.sh
   - phase: 1
   - agent: codex
   - status: open
   - spec: prometheus-context-detection-crossplatform.md:324-433
   - branch: feat/phase-1-docker-infra
   - depends_on: []
   ```
6. **Criar `.gitignore`** raiz do engine (excluir `data/`, `.env.local`, `__pycache__/`, `*.egg-info/`, `.venv/`).
7. **`git init`** em `/Users/samdev/dev/Prometheus/` e commit inicial (`chore: bootstrap project docs + agent context files`).

**Critérios de pronto:**
- `ls /Users/samdev/dev/Prometheus/` mostra: 6 specs `.md` originais, `EXECUTION_PLAN.md`, `CLAUDE.md`, `AGENTS.md` (symlink), `TASKS.md`, `.gitignore`, `.github/copilot-instructions.md`.
- Abrir sessão Claude Code no diretório → o modelo abre com CLAUDE.md já carregado.
- `codex` (CLI) no diretório → encontra AGENTS.md automaticamente.
- VS Code com Copilot → `.github/copilot-instructions.md` injeta contexto nas completions.
- Commit inicial feito.

Só depois disso as Fases 1-7 podem começar, já com os 3 agentes alinhados.

---

## Fase 0 — Fundação do vault (Dia 0, ~2h)

Bootstrap do vault antes de qualquer código. Entrega valor imediato: Claude Code já passa a ter contexto persistente na próxima sessão.

1. Criar `~/vault/` e inicializar git.
2. Criar estrutura de pastas conforme [prometheus-context-isolation.md](/Users/samdev/dev/Prometheus/prometheus-context-isolation.md):
   - `personal/{aerus-rpg,rpg-master-ai,linkedin-tool}/`
   - `career/{interviews,targets,applications,linkedin}/`
   - `knowledge/daily/{java,spring,kafka,python,ai-engineering,system-design}/`
   - `knowledge/deep/` (mesmas subpastas) — per [prometheus-knowledge-split.md](/Users/samdev/dev/Prometheus/prometheus-knowledge-split.md)
   - `work/avangrid/` com `.ctxguard` marcando barreira
   - `daily/` e `adrs/` na raiz
3. Escrever `~/vault/CLAUDE.md` copiando o conteúdo do bloco "CLAUDE.md Global" de [prometheus-vault-final.md:7-70](/Users/samdev/dev/Prometheus/prometheus-vault-final.md#L7-L70).
4. Escrever `~/vault/.gitignore` conforme [prometheus-vault-final.md:457-462](/Users/samdev/dev/Prometheus/prometheus-vault-final.md#L457-L462).
5. Escrever `.ctx` em cada pasta de contexto (`personal/.ctx`, `career/.ctx`, `knowledge/.ctx`, `work/.ctx` com flag RESTRICTED).
6. Escrever `CONTEXT.md` para cada projeto em `personal/` (usar template de [prometheus-vault-final.md:78-109](/Users/samdev/dev/Prometheus/prometheus-vault-final.md#L78-L109); exemplo preenchido do Aerus em `:113-162`).
7. Commit inicial: `git init && git commit -m "vault: initial structure"`.

**Verificação:** abrir sessão Claude Code em `~/vault/`, pedir "resuma o que é esse vault" — deve responder com base no CLAUDE.md e CONTEXT.md.

---

## Fase 1 — Infra Docker + bootstrap do engine (Dia 1-2)

1. Criar `~/prometheus/` com `pyproject.toml`, `src/`, `tests/`, `docker-compose.yml`, `.env.example`, `CLAUDE.md` do próprio engine.
2. Escrever `docker-compose.yml` consolidado de [prometheus-context-detection-crossplatform.md:324-381](/Users/samdev/dev/Prometheus/prometheus-context-detection-crossplatform.md#L324-L381) (profiles `gpu`/`cpu`). Serviços: qdrant, redis, neo4j, postgres, langfuse, ollama (gpu/cpu).
3. Escrever `setup.sh` de [prometheus-context-detection-crossplatform.md:395-433](/Users/samdev/dev/Prometheus/prometheus-context-detection-crossplatform.md#L395-L433) — detecta Mac vs PC e sobe o profile certo.
4. Escrever `src/config/platform.py` conforme :271-318 (detect_platform, embedding providers, keep_alive por plataforma).
5. Subir a stack: `./setup.sh`. Validar acesso:
   - Qdrant: `curl localhost:6333/collections`
   - Neo4j: `http://localhost:7474`
   - Langfuse: `http://localhost:3000`
   - Ollama: `ollama list`
6. Puxar modelos Ollama necessários: `gemma3:4b` (ou equivalente cabível), `phi3:mini` (classificador), `gemma3:27b` no PC (suggester).

**Verificação:** `docker compose ps` mostra todos healthy. `curl` em cada serviço retorna 200.

---

## Fase 2 — Store layer (Dia 3)

Camada L3 de [prometheus-context-engine.md:112-189](/Users/samdev/dev/Prometheus/prometheus-context-engine.md#L112-L189).

1. `src/store/vector_store.py` — cliente Qdrant, uma collection por contexto (`prometheus_personal`, `_career`, `_knowledge`, `_work`). Metadata por chunk: `file_path, language, chunk_type, symbol, project, git_commit, modified_at`.
2. `src/store/graph_store.py` — cliente Redis para grafo de dependências (`dep:<symbol>` → calls/called_by).
3. `src/store/session_store.py` — SQLite com schemas `adr`, `session_memory`, `code_change` ([prometheus-context-engine.md:158-187](/Users/samdev/dev/Prometheus/prometheus-context-engine.md#L158-L187)).
4. `src/store/collections.py` — função `get_search_collections(ctx)` com barreira work ([prometheus-context-isolation.md:154-171](/Users/samdev/dev/Prometheus/prometheus-context-isolation.md#L154-L171)).
5. Testes unitários: inserir/buscar em cada store.

**Verificação:** `pytest tests/store/` passando. Busca em collection `work` sem `ctx=work` retorna vazio.

---

## Fase 3 — Embedder + Watcher (Dia 4-6)

Camadas L1 e L2. **Ordem obrigatória: testes do chunker → chunker → embedder → watcher.** Ver D5 em "Decisões fixadas" para o rigor do chunker Java.

1. **Dia 4 (manhã):** montar `tests/embedder/fixtures/spring/` com os 30+ arquivos Spring reais (catálogo em D5). Escrever `tests/embedder/test_chunker_java.py` com assertions por fixture.
2. **Dia 4 (tarde):** `src/embedder/chunker.py` — tree-sitter Java primeiro. Iterar até a suite ficar 100% verde. Target padrão: arquivo Spring de 300 linhas → ~12 chunks de ~25 linhas.
3. **Dia 5 (manhã):** estender chunker para Python e TypeScript (tree-sitter-python, tree-sitter-typescript) com fixtures menores.
4. **Dia 5 (tarde):** `src/embedder/engine.py` — fastembed com `Snowflake/snowflake-arctic-embed-l-v2.0`. Providers dinâmicos vindos de `platform.py` (CUDA no PC, CoreML no Mac).
5. **Dia 6 (manhã):** `src/watcher/main.py` — watchdog monitorando os paths de [prometheus-context-engine.md:57-62](/Users/samdev/dev/Prometheus/prometheus-context-engine.md#L57-L62). Fila async para o pipeline.
6. **Dia 6 (tarde):** pipeline de ingest (watcher → chunker → embedder → vector_store) + script one-shot de indexação inicial.

**Verificação:** rodar indexação no `~/projects/aerus-rpg`, consultar Qdrant e confirmar chunks por método. Modificar um arquivo, ver re-ingest automático em <5s.

---

## Fase 4 — MCP Gateway mínimo (Dia 7-8)

Camada L5. Primeiro ganho real de contexto no Claude Code.

1. `src/mcp/server.py` com FastMCP. Ferramentas iniciais ([prometheus-context-engine.md:248-275](/Users/samdev/dev/Prometheus/prometheus-context-engine.md#L248-L275) + [prometheus-context-isolation.md:178-228](/Users/samdev/dev/Prometheus/prometheus-context-isolation.md#L178-L228)):
   - `search_code(query, ctx, language)` — com barreira work
   - `get_adrs(project, ctx)`
   - `save_adr(project, title, context, decision, rationale)`
   - `get_session_memory(project)`
   - `get_dependencies(symbol, project)`
2. Aplicar `CONTEXT_BUDGETS` diferenciado (claude-code 8k, copilot 2k) em :279-284.
3. Registrar MCP no Claude Code (`~/.config/claude-code/mcp.json` ou equivalente).
4. Smoke test: dentro do Claude Code pedir "busca `virtual threads` no knowledge" → deve retornar chunks do vault.

**Verificação:** Claude Code executa a tool `search_code` e retorna resultado real. Chamar `search_code` sem `ctx` nunca retorna nada de `work/`.

---

## Fase 5 — Context Detector + Router + CLI pb (Dia 9-11)

Juntos formam a experiência de uso diária.

1. `src/context/detector.py` — implementar `ContextDetector` de [prometheus-context-detection-crossplatform.md:32-163](/Users/samdev/dev/Prometheus/prometheus-context-detection-crossplatform.md#L32-L163). Sinais cwd (0.4), conteúdo (0.4), sessão (0.2). Proteção work na `:119-122`.
2. `src/router/classifier.py` — phi3:mini local, latência <100ms, retorna TaskType.
3. `src/router/engine.py` — LiteLLM com budget gate e roteamento por TaskType ([prometheus-context-engine.md:198-231](/Users/samdev/dev/Prometheus/prometheus-context-engine.md#L198-L231)). Integrar Langfuse.
4. Integrar detector+router no MCP: nova tool `ask(query, cwd, ctx?)` que detecta → roteia → responde, sempre com prefixo `[ctx N%]`.
5. `src/cli/pb.py` (typer) — comandos iniciais:
   - `pb ask "..."`
   - `pb search "..." [--ctx]`
   - `pb adr add|list`
   - `pb session <ctx>` (confirmação obrigatória para `work`)
   - `pb cost today|week`
6. Instalar `pb` como entry_point no sistema (pipx ou symlink).

**Verificação:** executar os exemplos de [prometheus-context-detection-crossplatform.md:233-250](/Users/samdev/dev/Prometheus/prometheus-context-detection-crossplatform.md#L233-L250). `cd ~/projects/aerus-rpg && pb ask "como está o combate"` → prefixa `[personal XX%]`. `pb session work` pede confirmação.

---

## Fase 6 — Knowledge automático: TIL → HOW-TO + deep suggester (Dia 12-13)

1. `src/vault/til_promoter.py` — código completo em [prometheus-vault-final.md:237-352](/Users/samdev/dev/Prometheus/prometheus-vault-final.md#L237-L352). Usa Ollama local (gemma local).
2. Git hook `~/vault/.git/hooks/post-commit` — :358-379.
3. Comandos CLI: `pb til`, `pb howto --from`, `pb til --list --pending`, `pb til --promote-today` per :386-402.
4. `src/vault/deep_suggester.py` — implementação de [prometheus-knowledge-split.md:128-188](/Users/samdev/dev/Prometheus/prometheus-knowledge-split.md#L128-L188). Cron semanal opcional ou `pb deep --suggest`.
5. `pb deep --list {seed,growing,solid}` para ver notas por maturidade.

**Verificação:** `pb til "records java não aceitam herança"` cria arquivo em `knowledge/daily/java/`. Commit → hook promove se tiver substância. `pb deep --suggest` retorna JSON válido com gaps.

---

## Fase 7 — Mem0 + memória de sessão comprimida (Dia 14-16)

1. Configurar Mem0 self-hosted apontando para Qdrant (`prometheus_<ctx>` collections) e Neo4j já rodando ([prometheus-context-isolation.md:256-287](/Users/samdev/dev/Prometheus/prometheus-context-isolation.md#L256-L287)).
2. Tool MCP `get_memory(query, ctx)` filtrando `ctx__ne: work` por padrão.
3. Session memory compressor: a cada 10 turns Haiku comprime histórico → `session_memory` no SQLite ([prometheus-context-engine.md:306-314](/Users/samdev/dev/Prometheus/prometheus-context-engine.md#L306-L314)).
4. Hook de fim-de-sessão: escreve resumo no daily note (per regra em `CLAUDE.md` global).

**Verificação:** conversar em sessão, fechar, abrir nova sessão. `get_memory` traz fatos da anterior. `session_memory` no SQLite tem entradas.

---

## Fase 8+ (pós-MVP) — Roadmap

Alinhado com [prometheus-second-brain-full.md:225-257](/Users/samdev/dev/Prometheus/prometheus-second-brain-full.md#L225-L257):

- **Fase 2 (sem 3-4):** grafo de deps completo no Redis; cross-encoder local para re-ranking (redução 85% de contexto); observabilidade Langfuse com dashboard custo por ctx.
- **Fase 3 (mês 2):** camada career (auto-posts LinkedIn, briefing de entrevista, tracker de vagas); git-aware change log (`code_change` populado via hook).
- **Fase 4 (mês 3+):** LinkedIn tool usa esse stack como infra; Aerus RPG e rpg-master-ai migram para o mesmo router.

---

## Arquivos críticos a criar

| Caminho | Propósito | Spec de referência |
|---|---|---|
| `~/vault/CLAUDE.md` | Instruções globais | vault-final.md:7-70 |
| `~/vault/*/CONTEXT.md` | Briefing por projeto | vault-final.md:78-162 |
| `~/prometheus/docker-compose.yml` | Stack | crossplatform.md:324-381 |
| `~/prometheus/setup.sh` | Bootstrap cross-OS | crossplatform.md:395-433 |
| `src/config/platform.py` | Detecção hardware | crossplatform.md:271-318 |
| `src/embedder/chunker.py` | **Componente mais crítico** (AST) | context-engine.md:94-102 |
| `src/embedder/engine.py` | Embedder fastembed | context-engine.md:83-92 |
| `src/watcher/main.py` | Watchdog | context-engine.md:51-70 |
| `src/store/{vector,graph,session}_store.py` | Persistência | context-engine.md:112-189 |
| `src/store/collections.py` | Barreira work | context-isolation.md:154-171 |
| `src/context/detector.py` | Inferência ctx | crossplatform.md:32-163 |
| `src/router/{engine,classifier}.py` | LiteLLM + phi3 | context-engine.md:198-231 |
| `src/mcp/server.py` | Gateway FastMCP | context-engine.md:242-275 |
| `src/cli/pb.py` | Terminal access | isolation.md:232-250, vault-final.md:386-402 |
| `src/vault/til_promoter.py` | Promoção TIL→HOW-TO | vault-final.md:237-352 |
| `src/vault/deep_suggester.py` | Gaps de conhecimento | knowledge-split.md:128-188 |

---

## Estratégia de execução multi-agente (Claude Code + Codex + Copilot)

**Princípio:** dividir por camada de complexidade (cada agente no que é força) + coordenar por branches de fase com PR interno (auditável, sem race). Copilot é sempre passivo no working tree ativo — nunca abre branch próprio.

### Mapa de responsabilidade por agente

| Agente | Tipo de trabalho | Componentes deste plano |
|---|---|---|
| **Claude Code** | Raciocínio profundo, decisões arquiteturais, MCP nativo, refactors multi-arquivo, TDD iterativo | Chunker Java + suite de fixtures (D5 / Fase 3), MCP Gateway (Fase 4), Context Detector com scoring (Fase 5), Router + classifier logic (Fase 5), Session Memory compressor (Fase 7), integrações que cruzam múltiplos módulos |
| **Codex (CLI)** | Boilerplate estrutural, config, scripts, CRUD, scaffolding | `docker-compose.yml` + `setup.sh` (Fase 1), `platform.py` (Fase 1), `vector/graph/session_store.py` (Fase 2), `watcher/main.py` (Fase 3 dia 6), `cli/pb.py` scaffolding typer (Fase 5), git hooks (Fase 6), `til_promoter.py` tradução direta da spec (Fase 6) |
| **Copilot (VS Code)** | Inline completion dentro do editor, preenche padrões que o contexto já estabeleceu | Assertions em test fixtures, type hints, imports, docstrings pequenas, pequenos utilitários que seguem padrão já escrito |

### Shared context / source of truth

Para os 3 lerem o mesmo contexto sem duplicação:

1. **`~/prometheus/CLAUDE.md`** — canônico. Descreve projeto, estado atual, convenções, IDs dos modelos, decisões fixadas (D1-D5).
2. **`~/prometheus/AGENTS.md`** — symlink para `CLAUDE.md` (Codex lê daqui automaticamente).
3. **`~/prometheus/.github/copilot-instructions.md`** — conteúdo reduzido do CLAUDE.md (Copilot lê inline no editor).
4. **`~/prometheus/TASKS.md`** — backlog por fase, com tag `agent: claude-code | codex | copilot` em cada task + status.

Qualquer mudança de convenção (ex.: trocar modelo Anthropic) atualiza CLAUDE.md e os 3 se alinham na próxima sessão.

### Workflow de coordenação (branches por fase + PR interno)

**Regra:** cada fase = uma branch. O agente responsável abre PR no final, você revisa e faz merge.

```
main
├── feat/phase-0-vault-bootstrap         ← manual ou Codex
├── feat/phase-1-docker-infra            ← Codex
├── feat/phase-2-store-layer             ← Codex
├── feat/phase-3a-chunker-java           ← Claude Code (TDD, crítico)
├── feat/phase-3b-embedder-watcher       ← Codex (depois que 3a mergear)
├── feat/phase-4-mcp-gateway             ← Claude Code
├── feat/phase-5a-detector-router        ← Claude Code
├── feat/phase-5b-cli-pb                 ← Codex
├── feat/phase-6-knowledge-automation    ← Codex
└── feat/phase-7-mem0-compressor         ← Claude Code
```

Copilot atua no working tree ativo da branch atual — quem quer que esteja editando aquele arquivo no VS Code ganha completions.

### Ordem de execução recomendada

Fases com dependências sequenciais não paralelizam. Mas algumas branches podem rodar em paralelo:

1. **Fase 0** (sequencial) — fundação do vault primeiro, sozinho.
2. **Fase 1 + Fase 2** (paralelizável) — docker-infra e store-layer não se tocam. Codex pode abrir as duas branches ao mesmo tempo.
3. **Fase 3a** (sequencial, bloqueia 3b) — Claude Code no chunker Java até suite 100% verde.
4. **Fase 3b + Fase 4 (setup inicial)** (parcialmente paralelo) — Codex termina embedder/watcher enquanto Claude Code já estuda FastMCP.
5. **Fase 5a + 5b** (paralelizável) — detector/router (Claude) e CLI scaffolding (Codex) independentes até a integração final.
6. **Fase 6 + Fase 7** (paralelizável) — knowledge automation (Codex) e Mem0/compressor (Claude Code) independentes.

### Protocolo de cada agente por sessão

**Claude Code:**
```
1. Ler ~/prometheus/CLAUDE.md + TASKS.md (branch da fase)
2. Pegar próxima task com agent: claude-code AND status: open
3. Criar/checkout branch feat/phase-N-<slug>
4. Implementar com TDD quando tem suite (Fase 3a especialmente)
5. Atualizar status: done no TASKS.md, commit, abrir PR
```

**Codex (CLI):**
```
1. Ler AGENTS.md (= CLAUDE.md) + TASKS.md
2. Pegar próxima task com agent: codex AND status: open
3. Criar/checkout branch feat/phase-N-<slug>
4. Implementar seguindo specs linkadas no TASKS.md
5. Atualizar status, commit, abrir PR
```

**Copilot (VS Code):**
```
Passivo. O humano controla; Copilot só completa inline. 
Regra: não aceitar sugestão que invente dependência/import não-existente.
Config: .github/copilot-instructions.md com resumo de D1-D5 + stack Python 3.12.
```

### Qualidade e guard rails

- **PRs sempre rodam** `pytest` e `ruff check` em CI local (pre-push hook) antes do merge.
- **Fase 3a tem gate especial:** só faz merge se `pytest tests/embedder/` estiver 100% verde em 30+ fixtures.
- **Copilot não edita arquivos de spec** (`/Users/samdev/dev/Prometheus/*.md`) nem `CLAUDE.md` raiz.
- **Branches work-relacionadas** (qualquer coisa que toque `collections.py::work` ou `.ctxguard`) só são modificadas por Claude Code — barreira human-in-the-loop.
- **TASKS.md é truth:** quando dois agentes divergem sobre ordem, TASKS.md desempata.

### Custo operacional estimado

- Claude Code: ~60% do volume de trabalho, 70% dos tokens cloud (Sonnet 4.6 padrão, Opus 4.7 só em Fase 3a e 4).
- Codex: ~30% do volume, 25% dos tokens.
- Copilot: ~10% do volume, custo fixo da subscription.
- Total estimado das 2-3 semanas: ~$15-25 em API calls no mix.

---

## Verificação end-to-end do MVP

Ao final da Fase 7, os seguintes fluxos devem funcionar sem intervenção:

1. **Vault com contexto persistente**
   - `cd ~/vault && claude` → Claude lê CLAUDE.md + daily note do dia + CONTEXT.md ativo.
2. **Busca no codebase via Claude Code**
   - Em Claude Code: "busque como configurei Kafka no Aerus" → retorna chunks reais via MCP.
3. **Barreira work funciona**
   - `pb search "EKS"` → retorna vazio de `work/`.
   - `pb session work` → pede confirmação, depois `pb search "EKS" --ctx=work` retorna.
4. **Detecção automática de contexto**
   - `pb ask "virtual threads java 21"` → `[knowledge XX%] ...`
   - `cd ~/projects/aerus-rpg && pb ask "combate"` → `[personal XX%] ...`
5. **Roteamento de modelo economiza tokens**
   - Pergunta trivial → Haiku (confirmado em Langfuse).
   - `pb cost today` reporta <$0.10 em dia típico.
6. **Knowledge loop**
   - `pb til "..."` cria arquivo. Commit → hook promove se tiver substância.
   - `pb deep --suggest` retorna gaps válidos.
7. **Memória entre sessões**
   - Sessão 1: decidir usar Qdrant over pgvector → `save_adr`. Fechar.
   - Sessão 2: `pb ask "por que Qdrant?"` → resposta cita o ADR.
8. **Cross-platform**
   - Mesmo `setup.sh` sobe correto no Mac (profile cpu) e PC (profile gpu).

---

## Decisões fixadas (antes da execução)

Estas decisões substituem a ambiguidade das specs originais e valem como contrato do MVP:

### D1 — Paths canônicos
- Dados: `~/vault/` (Obsidian, git-tracked)
- Engine + specs + docs de agentes: `/Users/samdev/dev/Prometheus/` (Python, Docker, MCP server, CLI, especificações originais, arquivos de contexto dos agentes)
- Env vars: `PROMETHEUS_VAULT=~/vault`, `PROMETHEUS_ENGINE=/Users/samdev/dev/Prometheus`.
- Nunca usar caminhos absolutos intercambiáveis — sempre relativos a esses dois roots.

### D2 — Modelos Anthropic (config LiteLLM)
| TaskType | Modelo | ID |
|---|---|---|
| TRIVIAL_COMPLETION | Haiku 4.5 | `claude-haiku-4-5-20251001` |
| CODE_ANALYSIS | Sonnet 4.6 | `claude-sonnet-4-6` |
| ARCHITECTURE / DEEP_REASONING | Opus 4.7 (com budget explícito) | `claude-opus-4-7` |
| Fallback | Haiku 4.5 | `claude-haiku-4-5-20251001` |

Atualizar `src/router/engine.py` para usar esses IDs; ignorar os `4-5` antigos das specs.

### D3 — Modelos Ollama
Manter `gemma4:e4b` (primário/classificador/promoter) e `gemma4:26b` (deep suggester, só no PC). `phi3:mini` continua como classificador rápido <100ms na Fase 5.

### D4 — Backend de grafo
- **Redis** — grafo de dependências de código (`dep:<symbol>` → calls/called_by). Entra na Fase 3.
- **Neo4j** — exclusivamente para relações do Mem0. Entra na Fase 7.
- Não tentar unificar os dois no MVP.

### D5 — Chunker Java (investimento pesado upfront)
Conforme risco levantado em [prometheus-context-engine.md:423](/Users/samdev/dev/Prometheus/prometheus-context-engine.md#L423), o chunker ganha rigor extra antes de qualquer outra coisa da Fase 3:

1. **Suite de testes primeiro (TDD).** Montar `tests/embedder/fixtures/spring/` com 30+ arquivos reais cobrindo:
   - `@RestController` / `@Service` / `@Repository` / `@Component` padrão
   - Inner classes (static e non-static)
   - Anonymous classes (callbacks, lambdas de builders)
   - Records (Java 14+) com compact constructors
   - Generics com bounded types e wildcards
   - Lambdas complexas (Stream pipelines multi-linha)
   - `@Transactional` com self-invocation (caso real do knowledge)
   - Classes com 500+ linhas (OrderService típico do Avangrid)
   - Interfaces com default/static methods
   - Enums com métodos
   - Annotations custom
2. **Para cada fixture, assertion explícita sobre:**
   - Contagem de chunks esperada
   - Boundary byte-exato do primeiro e último chunk de cada método
   - Metadata correta (`symbol`, `chunk_type`, linha inicial/final)
   - Chunks órfãos (código fora de método) não viram chunks válidos
3. **2h dedicadas antes de escrever embedder/engine.py.** Chunker tem que passar 100% da suite antes de sair para o Qdrant.
4. Reusar a mesma estrutura de testes para Python (via tree-sitter-python) e TypeScript (tree-sitter-typescript), mas com fixtures menores inicialmente (10 arquivos cada).

Efeito no cronograma: Fase 3 passa de "Dia 4-5" para **"Dia 4-6"** (3 dias), empurrando MCP para Dia 7-8 e MVP completo para **Dia 15-16** em vez de 14.
