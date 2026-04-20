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
| TaskType | ID |
|---|---|
| TRIVIAL_COMPLETION | `claude-haiku-4-5-20251001` |
| CODE_ANALYSIS | `claude-sonnet-4-6` |
| ARCHITECTURE / DEEP_REASONING | `claude-opus-4-7` (com budget explícito) |
| Fallback | `claude-haiku-4-5-20251001` |

Ignorar os nomes `claude-*-4-5` das specs originais — estão desatualizados.

### D3 — Modelos Ollama
- `gemma4:e4b` — primário / classificador / TIL promoter.
- `gemma4:26b` — deep suggester (somente no PC com VRAM suficiente).
- `phi3:mini` — classificador rápido (<100ms) no router da Fase 5.

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

## Referências rápidas

- README e uso: [README.md](README.md)
- Backlog: [TASKS.md](TASKS.md) — todas `done` (MVP completo)
- Arquitetura 5-layer: [prometheus-context-engine.md](prometheus-context-engine.md)
- Estrutura do vault + barreira work: [prometheus-context-isolation.md](prometheus-context-isolation.md)
- Context detector + cross-platform: [prometheus-context-detection-crossplatform.md](prometheus-context-detection-crossplatform.md)
- TIL→HOW-TO + knowledge split: [prometheus-knowledge-split.md](prometheus-knowledge-split.md)
- Roadmap e stack full: [prometheus-second-brain-full.md](prometheus-second-brain-full.md)
- Vault templates: [prometheus-vault-final.md](prometheus-vault-final.md)
