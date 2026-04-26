# Prometheus — Segundo Cérebro Self-Hosted

Engine Python do segundo cérebro do Sammy Junior (Senior Full Stack Engineer, Java + React, João Pessoa, BR).

Prometheus existe para transformar conhecimento espalhado em contexto recuperável com baixo atrito: notas do vault, decisões arquiteturais, TILs, HOW-TOs, memória de sessão e dependências de código passam a ficar acessíveis por CLI, MCP e automações locais.

Em vez de reabrir dezenas de abas, reler chats antigos ou reenviar contexto bruto para modelos grandes, o Prometheus mantém um vault externo em `~/vault`, indexa esse conteúdo e devolve apenas o contexto útil para a tarefa atual.

---

## O que é

Prometheus é um sistema local de contexto para trabalho técnico contínuo.

Ele combina:

- vault em Markdown para conhecimento e decisões
- indexação semântica com Qdrant
- grafo de dependências para código
- memória comprimida de sessão
- compressão de contexto para reduzir tokens
- acesso por `pb` CLI e MCP para Claude Code / Copilot

O objetivo não é ser um gerador genérico de notas. O objetivo é reduzir perda de contexto, acelerar retomada de trabalho e diminuir custo de tokens em tarefas reais de engenharia.

## Para quem serve

Prometheus foi desenhado para um fluxo de trabalho de engenharia sênior/staff com múltiplos contextos ativos ao mesmo tempo:

- backend e arquitetura
- carreira e entrevistas
- projetos pessoais
- contexto corporativo isolado

Se o problema principal é "tenho informação demais, espalhada demais, e modelos estão consumindo contexto demais para recuperar o essencial", esse projeto faz sentido.

## Para qual finalidade usar

Casos de uso principais:

- recuperar rapidamente decisões, padrões e notas técnicas do vault
- registrar TILs e promover conhecimento recorrente para HOW-TO
- manter contexto de projeto entre sessões longas
- apoiar entrevistas, escrita técnica e arquitetura com memória local
- isolar contexto sensível de `work` sem misturar com `personal` ou `knowledge`
- comprimir contexto antes de enviar informação para modelos maiores

## Como o sistema funciona

Fluxo operacional resumido:

1. O conhecimento fica fora do repositório, em `~/vault`.
2. O Prometheus indexa esse conteúdo por contexto (`knowledge`, `career`, `personal`, `work`).
3. A CLI `pb` e o servidor MCP consultam esse índice.
4. O contexto recuperado é comprimido pelo Caveman Compressor (phi3:mini) antes de ser entregue.
5. O modelo recebe menos ruído e mais sinal.

Unifica 4 contextos de vida/trabalho num grafo consultável por Claude Code, Copilot e terminal (`pb` CLI):

| Contexto    | O que guarda                                    |
| ----------- | ----------------------------------------------- |
| `knowledge` | HOW-TOs, TILs, referências técnicas             |
| `career`    | prep de entrevistas, empresas, metas            |
| `personal`  | projetos pessoais, decisões, notas              |
| `work`      | código proprietário Avangrid (barreira isolada) |

## Configurações essenciais

As decisões abaixo são estruturais para o projeto:

| Configuração                | Valor esperado                 | Finalidade                      |
| --------------------------- | ------------------------------ | ------------------------------- |
| `PROMETHEUS_VAULT`          | `~/vault`                      | Vault externo com dados e notas |
| `PROMETHEUS_ENGINE`         | `/Users/samdev/dev/Prometheus` | Caminho do engine               |
| `QDRANT_URL`                | `http://localhost:6333`        | Busca vetorial                  |
| `REDIS_URL`                 | `redis://localhost:6379`       | Grafo de dependências           |
| `PROMETHEUS_OLLAMA_LOCAL_HOST` | `http://127.0.0.1:11434`    | Modelos locais                  |
| `PROMETHEUS_RTK_MAX_TOKENS` | `300` a `450`                  | Teto de tokens do Caveman Compressor |
| `PROMETHEUS_DAILY_BUDGET`   | ex. `5.0`                      | Budget diário de uso cloud      |
| `PROMETHEUS_OPUS_BUDGET`    | ex. `2.0`                      | Budget específico para Opus     |
| `PROMETHEUS_EXPANSION_SOURCE_CATALOG` | `config/expansion_sources.json` | Catálogo de fontes registradas |
| `PROMETHEUS_EXPANSION_MONTHLY_BUDGET` | `4.0`                  | Budget mensal da expansão cloud |

Regra importante: `~/vault` e o engine nunca devem ser misturados. O vault é a camada de dados. Este repositório é a camada de runtime.

---

## Arquitetura

```
~/vault/              ← Obsidian vault (dados, jamais mistura com engine)
│   knowledge/
│   career/
│   personal/
│   work/             ← barreira .ctxguard — acesso requer ctx explícito
│   adrs/
│   daily/
└── CLAUDE.md

/Users/samdev/dev/Prometheus/    ← este repositório (engine)
├── src/prometheus/
│   ├── cli/          ← pb CLI (typer)
│   ├── config/       ← runtime config + platform detection
│   ├── context/      ← ContextDetector (scoring cwd/content/session)
│   ├── embedder/     ← chunker (Java/Python/TS) + fastembed engine
│   ├── expansion/    ← coleta, scoring, staging, review gate e budget
│   ├── mcp/          ← MCP Gateway (FastMCP, tools para Claude Code)
│   ├── memory/       ← Mem0 self-hosted + session compressor
│   ├── observability/← compliance e integração com traces
│   ├── policy/       ← guardrails de contexto e cloud
│   ├── resilience/   ← circuit breaker e proteções de runtime
│   ├── router/       ← classifier (phi3:mini) + LiteLLM engine + Caveman Compressor
│   ├── store/        ← VectorStore (Qdrant), GraphStore (Redis), SessionStore (SQLite)
│   ├── vault/        ← TIL promoter + deep suggester
│   └── watcher/      ← watchdog → reindexa ao salvar
├── tests/
│   ├── cli/          ← contrato principal da CLI
│   ├── config/       ← runtime e expansion config
│   ├── context/      ← cache key e detecção de contexto
│   ├── embedder/     ← chunker Java/Python/TS
│   ├── expansion/    ← collector, scoring, security e integração
│   ├── policy/       ← work barrier e registry
│   ├── resilience/   ← circuit breaker
│   ├── router/       ← provider validation e budget guardrails
│   └── store/        ← stores e collections
├── docker-compose.yml
├── setup.sh
└── pyproject.toml
```

### Stack

| Camada          | Tecnologia                                                 |
| --------------- | ---------------------------------------------------------- |
| Embeddings      | `fastembed` + BAAI/bge-small-en-v1.5 (384d, Apple Silicon) |
| Vector DB       | Qdrant (local, Docker)                                     |
| Graph deps      | Redis (`dep:<symbol>` → calls/called_by)                   |
| Graph mem       | Neo4j (exclusivo Mem0)                                     |
| Session         | SQLite via aiosqlite                                       |
| LLM routing     | LiteLLM + Langfuse                                         |
| Compressão      | Caveman Compressor (`phi3:mini` via LiteLLM, async)        |
| Chunker         | tree-sitter (Java / Python / TypeScript)                   |
| CLI             | Typer + pipx                                               |
| MCP             | FastMCP                                                    |
| Observabilidade | Langfuse (self-hosted)                                     |

---

## Hardware

| Máquina                                 | Config   | Profile Docker |
| --------------------------------------- | -------- | -------------- |
| Mac M1 16GB                             | CPU only | `cpu`          |
| PC Ryzen 7 5800X3D + RTX 4070 Ti + 32GB | GPU      | `gpu`          |

O sistema roda idêntico nos dois, apenas alternando o profile.

---

## Infra local (Docker Compose)

```bash
docker compose --profile cpu up -d   # Mac
docker compose --profile gpu up -d   # PC
```

Serviços:

| Serviço             | Porta       |
| ------------------- | ----------- |
| Qdrant              | 6333        |
| Redis               | 6379        |
| Neo4j               | 7474 / 7687 |
| Postgres (Langfuse) | 5432        |
| Langfuse            | 3000        |
| Ollama              | 11434       |

## Modos de uso

Prometheus funciona em três modos complementares:

### 1. CLI local

Para consultas, indexação, captura de TIL, promoção para HOW-TO e operação direta do vault.

### 2. MCP para IDEs

Para Claude Code e Copilot consumirem contexto do vault dentro da sessão atual, com budgets específicos por caller.

### 3. Watcher contínuo

Para manter o índice sincronizado enquanto o vault muda ao longo do dia.

---

## Instalação

```bash
# 1. Clonar e entrar no diretório
git clone <repo> /Users/samdev/dev/Prometheus
cd /Users/samdev/dev/Prometheus

# 2. Subir infra
./setup.sh          # detecta plataforma, baixa modelos Ollama, sobe Docker

# 3. Instalar CLI
pipx install --editable .

# 4. Carregar ambiente local desta máquina
set -a
source .env.local
set +a

# 5. Verificar
pb --help
```

Depois da instalação do engine, inicialize o vault externo seguindo `docs/VAULT_SETUP.md`.

Sequência recomendada em máquina nova:

1. subir a stack com `./setup.sh`
2. instalar a CLI com `pipx install --editable .`
3. carregar `.env.local` no shell atual
4. preparar `~/vault`
5. indexar `knowledge`, `career` e `personal`
6. ativar watcher ou fluxo manual de indexação

Observação: `setup.sh` gera `.env.local`, mas não exporta essas variáveis para o shell atual. Para usar `pb` com os paths e hosts corretos, carregue `.env.local` explicitamente ou use um loader de env da sua shell.

### macOS — boot automático

O LaunchAgent `~/Library/LaunchAgents/dev.samdev.colima.plist` sobe automaticamente:

1. Colima (runtime Docker)
2. `docker compose --profile cpu up -d`
3. `pb watch ~/vault/knowledge --ctx knowledge` (watcher em background)

---

## Uso rápido

```bash
# Consulta ao segundo cérebro (detecta contexto automaticamente)
pb ask "como configurar Spring @Transactional com self-invocation"

# Busca semântica direta
pb search "UUID qdrant" --ctx knowledge

# Indexação one-shot
pb index ~/vault/knowledge --ctx knowledge

# Watcher contínuo
pb watch ~/vault/knowledge --ctx knowledge

# Registrar aprendizado
pb til "Spring @Transactional não funciona em self-invocation"

# Promover TILs do dia em HOW-TOs
pb til --promote-today

# ADR
pb adr add --project prometheus --title "usar UUID5 como ID no Qdrant em vez de SHA1"
pb adr list --project prometheus

# Sugestões de aprofundamento
pb deep suggest

# Custo de LLMs
pb cost today

# Expansão manual com staging obrigatório
pb expand run --ctx knowledge --topic "vector search" --fast
pb expand review ~/vault/knowledge/staging/vector-search.md
pb expand approve ~/vault/knowledge/staging/vector-search.md
# ou:
pb expand reject ~/vault/knowledge/staging/vector-search.md
```

## Documentação para Vault

- Bootstrap do vault: `docs/VAULT_SETUP.md`
- Mapa de publicação por contexto: `docs/VAULT_PUBLISHING_MAP.md`
- Auditoria e pendências de documentação: `docs/AUDIT_REPORT.md`

---

## Modelos LLM

### Anthropic (via LiteLLM)

| Task Type                         | Modelo                      |
| --------------------------------- | --------------------------- |
| Trivial / completion              | `claude-haiku-4-5-20251001` |
| Análise de código                 | `claude-sonnet-4-6`         |
| Arquitetura / raciocínio profundo | `claude-opus-4-7`           |
| Fallback                          | `claude-haiku-4-5-20251001` |

### Ollama (local)

| Modelo       | Uso                           | Hardware         |
| ------------ | ----------------------------- | ---------------- |
| `gemma4:e4b` | scoring local, TIL promoter   | Mac + PC         |
| `phi3:mini`  | classificador rápido (<100ms) + Caveman Compressor | Mac + PC         |
| `gemma4:26b` | deep suggester                | PC (RTX 4070 Ti) |

---

## MCP — integração com Claude Code

O servidor MCP expõe tools para Claude Code / Copilot consumirem o vault em tempo real:

| Tool                 | Descrição                                     |
| -------------------- | --------------------------------------------- |
| `search_code`        | busca semântica no contexto ativo             |
| `get_adrs`           | lista ADRs do projeto                         |
| `save_adr`           | persiste nova decisão                         |
| `get_session_memory` | recupera memória de sessão comprimida         |
| `get_dependencies`   | grafo de deps de código via Redis             |
| `ask`                | query completa: detector → retrieval → Caveman Compressor → prompts |

Registro (já configurado):

```bash
claude mcp list   # deve exibir: prometheus ✓ Connected
```

---

## Testes

```bash
python3 -m pytest tests/ -q
```

Suites atuais no repositório:

| Suite                | Foco principal                                       |
| -------------------- | ---------------------------------------------------- |
| `tests/cli/`         | CLI principal (`ask`, `search`, `index`, `watch`)    |
| `tests/config/`      | runtime config e expansion paths                     |
| `tests/context/`     | cache key e detecção de contexto                     |
| `tests/embedder/`    | chunker Java/Python/TS                               |
| `tests/expansion/`   | collector, scoring, staging, review gate e security  |
| `tests/policy/`      | work barrier e registry                              |
| `tests/resilience/`  | circuit breaker                                      |
| `tests/router/`      | provider validation, budget guardrails e Caveman Compressor |
| `tests/store/`       | stores e collections                                 |

O gate da Fase 3a (chunker Java 100% green) é pré-requisito para merge.

---

## Convenções

- Python 3.11+ com type hints sempre
- `dataclass` > dict
- Async por padrão em I/O
- Testes de integração com Testcontainers (sem mocks de repositório)
- IDs Qdrant: `uuid.uuid5(uuid.NAMESPACE_URL, key)` (SHA1 hex rejeitado)
- `SessionStore`: chamar `.init()` explicitamente — não tem `async with`

---

## Arquivos protegidos

Nunca editar:

- `CLAUDE.md` / `AGENTS.md`
- `TASKS.md`
- `prometheus-*.md` (specs originais)

Nunca acessar sem `ctx=work` explícito:

- `~/vault/work/`
- Collections `work` do Qdrant

---

## Observabilidade

Langfuse dashboard: [http://localhost:3000](http://localhost:3000)

Rastreia: custo por modelo, latência, chamadas por contexto, sessões.
