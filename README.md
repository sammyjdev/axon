# Prometheus — Segundo Cérebro Self-Hosted

Engine Python do segundo cérebro do Sammy Junior (Senior Full Stack Engineer, Java + React, João Pessoa, BR).

Unifica 4 contextos de vida/trabalho num grafo consultável por Claude Code, Copilot e terminal (`pb` CLI):

| Contexto | O que guarda |
|---|---|
| `knowledge` | HOW-TOs, TILs, referências técnicas |
| `career` | prep de entrevistas, empresas, metas |
| `personal` | projetos pessoais, decisões, notas |
| `work` | código proprietário Avangrid (barreira isolada) |

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
│   ├── config/       ← platform detection (Mac M1 / PC GPU)
│   ├── context/      ← ContextDetector (scoring cwd/content/session)
│   ├── embedder/     ← chunker (Java/Python/TS) + fastembed engine
│   ├── mcp/          ← MCP Gateway (FastMCP, tools para Claude Code)
│   ├── memory/       ← Mem0 self-hosted + session compressor
│   ├── router/       ← classifier (phi3:mini) + LiteLLM engine
│   ├── store/        ← VectorStore (Qdrant), GraphStore (Redis), SessionStore (SQLite)
│   ├── vault/        ← TIL promoter + deep suggester
│   └── watcher/      ← watchdog → reindexa ao salvar
├── tests/
│   ├── cli/          ← 4 testes CLI (pb ask/search/index/watch)
│   ├── embedder/     ← 118 testes chunker Java (gate D5)
│   └── store/        ← 17 testes stores
├── docker-compose.yml
├── setup.sh
└── pyproject.toml
```

### Stack

| Camada | Tecnologia |
|---|---|
| Embeddings | `fastembed` + BAAI/bge-small-en-v1.5 (384d, Apple Silicon) |
| Vector DB | Qdrant (local, Docker) |
| Graph deps | Redis (`dep:<symbol>` → calls/called_by) |
| Graph mem | Neo4j (exclusivo Mem0) |
| Session | SQLite via aiosqlite |
| LLM routing | LiteLLM + Langfuse |
| Chunker | tree-sitter (Java / Python / TypeScript) |
| CLI | Typer + pipx |
| MCP | FastMCP |
| Observabilidade | Langfuse (self-hosted) |

---

## Hardware

| Máquina | Config | Profile Docker |
|---|---|---|
| Mac M1 16GB | CPU only | `cpu` |
| PC Ryzen 7 5800X3D + RTX 4070 Ti + 32GB | GPU | `gpu` |

O sistema roda idêntico nos dois, apenas alternando o profile.

---

## Infra local (Docker Compose)

```bash
docker compose --profile cpu up -d   # Mac
docker compose --profile gpu up -d   # PC
```

Serviços:

| Serviço | Porta |
|---|---|
| Qdrant | 6333 |
| Redis | 6379 |
| Neo4j | 7474 / 7687 |
| Postgres (Langfuse) | 5432 |
| Langfuse | 3000 |
| Ollama | 11434 |

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

# 4. Verificar
pb --help
```

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
pb til add "Spring @Transactional não funciona em self-invocation"

# Promover TILs do dia em HOW-TOs
pb til promote-today

# ADR
pb adr add "usar UUID5 como ID no Qdrant em vez de SHA1"
pb adr list

# Sugestões de aprofundamento
pb deep suggest --ctx knowledge

# Custo de LLMs
pb cost show
```

---

## Modelos LLM

### Anthropic (via LiteLLM)

| Task Type | Modelo |
|---|---|
| Trivial / completion | `claude-haiku-4-5-20251001` |
| Análise de código | `claude-sonnet-4-6` |
| Arquitetura / raciocínio profundo | `claude-opus-4-7` |
| Fallback | `claude-haiku-4-5-20251001` |

### Ollama (local)

| Modelo | Uso | Hardware |
|---|---|---|
| `gemma4:e4b` | primário, TIL promoter | Mac + PC |
| `phi3:mini` | classificador rápido (<100ms) | Mac + PC |
| `gemma4:26b` | deep suggester | PC (RTX 4070 Ti) |

---

## MCP — integração com Claude Code

O servidor MCP expõe tools para Claude Code / Copilot consumirem o vault em tempo real:

| Tool | Descrição |
|---|---|
| `search_code` | busca semântica no contexto ativo |
| `get_adrs` | lista ADRs do projeto |
| `save_adr` | persiste nova decisão |
| `get_session_memory` | recupera memória de sessão comprimida |
| `get_dependencies` | grafo de deps de código via Redis |
| `get_memory` | memória Mem0 com filtro de contexto |
| `ask` | query completa: detector → router → retrieval |

Registro (já configurado):
```bash
claude mcp list   # deve exibir: prometheus ✓ Connected
```

---

## Testes

```bash
python3 -m pytest tests/ -q
# 139 passed
```

| Suite | Testes | Cobertura |
|---|---|---|
| `tests/embedder/` | 118 | Chunker Java (30+ fixtures Spring) |
| `tests/store/` | 17 | VectorStore, GraphStore, SessionStore |
| `tests/cli/` | 4 | pb ask / search / index / watch |

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
- `EXECUTION_PLAN.md`, `TASKS.md`
- `prometheus-*.md` (specs originais)

Nunca acessar sem `ctx=work` explícito:
- `~/vault/work/`
- Collections `work` do Qdrant

---

## Observabilidade

Langfuse dashboard: [http://localhost:3000](http://localhost:3000)

Rastreia: custo por modelo, latência, chamadas por contexto, sessões.
