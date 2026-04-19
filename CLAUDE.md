# Prometheus — Agent Context (canonical)

Este é o arquivo canônico de contexto para qualquer agente (Claude Code, Codex, Copilot) trabalhando neste projeto. Leia antes de executar qualquer ação.

## Visão do projeto

**Prometheus** é um "segundo cérebro" self-hosted do Sammy Junior (Senior Full Stack Engineer, Java + React, João Pessoa, BR). Unifica 4 contextos (projetos pessoais, carreira, conhecimento técnico, trabalho/Avangrid) num único grafo consultável por Claude Code, Copilot e terminal (`pb` CLI).

Hardware alvo: Ryzen 7 5800X3D + RTX 4070 Ti + 32GB (PC) e Mac M1 16GB. O sistema roda idêntico nos dois, apenas alternando o profile Docker (`gpu` vs `cpu`).

## Estado atual

**Greenfield.** O diretório contém apenas as 6 especificações em markdown (`prometheus-*.md`) e estes arquivos de contexto de agentes. Nenhum código Python, nenhum Docker rodando, nenhum vault criado.

## Entry points para agentes

- **Plano de execução passo a passo:** [EXECUTION_PLAN.md](EXECUTION_PLAN.md) — leia antes de pegar qualquer task.
- **Backlog com atribuição por agente:** [TASKS.md](TASKS.md) — pegue a próxima task com `agent:` igual ao seu tipo e `status: open`.
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

- **Linguagem:** Python 3.12 (engine), Bash (setup), Markdown (vault).
- **Infra local:** Docker Compose com Qdrant, Redis, Neo4j, Postgres, Langfuse, Ollama (profiles `gpu`/`cpu`).
- **Libs-chave:** `fastembed`, `qdrant-client`, `redis`, `watchdog`, `tree-sitter-java/python/typescript`, `litellm`, `mcp` (FastMCP), `typer` (CLI), `ollama`.

## Convenções de código

- Type hints sempre (Python 3.12).
- `dataclass` > dict; records Java > classes anêmicas; virtual threads para I/O.
- Sem Lombok. Sem comentários óbvios — código autodocumentado.
- Testes: Testcontainers para integração, sem mocks de repositório.
- Sem comentários explicando o "quê" (o nome do símbolo já faz isso). Comente só "porquê" não-óbvio.

## Regras de navegação por agente

### Claude Code (principal, raciocínio profundo)
Responsável por: Chunker Java (D5 / Fase 3a), MCP Gateway (Fase 4), Context Detector (Fase 5), Router + classifier (Fase 5), Session Memory compressor (Fase 7), integrações multi-módulo.

Protocolo:
1. Ler `CLAUDE.md` + `EXECUTION_PLAN.md` + `TASKS.md`.
2. Pegar próxima task com `agent: claude-code AND status: open`.
3. Criar branch `feat/phase-N-<slug>`.
4. Implementar com TDD quando há suite (Fase 3a especialmente).
5. Atualizar `status: done` no TASKS.md, commit, abrir PR interno.

### Codex CLI (boilerplate estrutural)
Responsável por: `docker-compose.yml` + `setup.sh` (Fase 1), `platform.py` (Fase 1), stores (Fase 2), watcher (Fase 3 dia 6), CLI `pb` scaffolding (Fase 5), git hooks (Fase 6), `til_promoter.py` (Fase 6).

Protocolo: lê `AGENTS.md` (symlink → `CLAUDE.md`), segue o mesmo fluxo de branches.

### Copilot (passivo, inline no editor)
Nunca abre branch próprio. Nunca edita `CLAUDE.md`, `AGENTS.md`, `EXECUTION_PLAN.md`, `TASKS.md`, ou qualquer `prometheus-*.md` (specs). Config em `.github/copilot-instructions.md`.

Regras estritas:
- Não aceitar sugestão que invente dependência/import inexistente.
- Não sugerir código que contradiz D1-D5.

## Proibições universais (qualquer agente)

1. **Nunca editar** as 6 specs originais (`/Users/samdev/dev/Prometheus/prometheus-*.md`). São fonte imutável.
2. **Nunca misturar** dados do vault (`~/vault/`) com código do engine (`/Users/samdev/dev/Prometheus/`).
3. **Nunca acessar** `~/vault/work/` ou collections `work` do Qdrant sem `ctx=work` explícito. Barreira protege propriedade intelectual da Avangrid.
4. **Nunca escrever** código proprietário da Avangrid em qualquer lugar do vault ou do engine.
5. **Nunca ignorar** falha de hook ou teste — investigar causa raiz, não silenciar.
6. **Nunca fazer commit** com credenciais, tokens, `.env` ou dados de cliente.

## Workflow de branches

```
main
├── feat/phase-0-vault-bootstrap
├── feat/phase-1-docker-infra
├── feat/phase-2-store-layer
├── feat/phase-3a-chunker-java     (Claude Code, TDD crítico)
├── feat/phase-3b-embedder-watcher
├── feat/phase-4-mcp-gateway       (Claude Code)
├── feat/phase-5a-detector-router  (Claude Code)
├── feat/phase-5b-cli-pb
├── feat/phase-6-knowledge-automation
└── feat/phase-7-mem0-compressor   (Claude Code)
```

Cada PR roda `pytest` + `ruff check` localmente via pre-push hook antes do merge.

## Gate especial da Fase 3a

Branch `feat/phase-3a-chunker-java` **só merge** se `pytest tests/embedder/` estiver 100% verde nas 30+ fixtures Java. Este é o único gate bloqueante do MVP.

## Referências rápidas

- Plano completo: [EXECUTION_PLAN.md](EXECUTION_PLAN.md)
- Backlog: [TASKS.md](TASKS.md)
- Arquitetura 5-layer: [prometheus-context-engine.md](prometheus-context-engine.md)
- Estrutura do vault + barreira work: [prometheus-context-isolation.md](prometheus-context-isolation.md)
- Context detector + cross-platform: [prometheus-context-detection-crossplatform.md](prometheus-context-detection-crossplatform.md)
- TIL→HOW-TO + knowledge split: [prometheus-knowledge-split.md](prometheus-knowledge-split.md)
- Roadmap e stack full: [prometheus-second-brain-full.md](prometheus-second-brain-full.md)
- Vault templates: [prometheus-vault-final.md](prometheus-vault-final.md)
