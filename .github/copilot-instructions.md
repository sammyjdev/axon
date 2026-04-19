# Copilot Instructions — Prometheus

Você está dentro de `/Users/samdev/dev/Prometheus/`, engine Python do segundo cérebro do Sammy. Leia `CLAUDE.md` na raiz se precisar de contexto completo — este arquivo é apenas o resumo operacional.

## Stack
- Python 3.12, type hints sempre.
- Libs principais: `fastembed`, `qdrant-client`, `redis`, `watchdog`, `tree-sitter-java/python/typescript`, `litellm`, `mcp` (FastMCP), `typer`.
- Infra local via Docker Compose (Qdrant + Redis + Neo4j + Postgres + Langfuse + Ollama).

## Decisões travadas (D1–D5)

1. **Paths:** dados em `~/vault/`, engine em `/Users/samdev/dev/Prometheus/`. Não inverter.
2. **Modelos Anthropic:** `claude-haiku-4-5-20251001`, `claude-sonnet-4-6`, `claude-opus-4-7`. Ignorar `4-5` das specs antigas.
3. **Modelos Ollama:** `gemma4:e4b`, `gemma4:26b`, `phi3:mini`.
4. **Grafo:** Redis para deps de código, Neo4j só para Mem0.
5. **Chunker Java:** TDD-first com 30+ fixtures Spring; só merge com 100% green.

## Convenções de código

- `dataclass` > dict.
- Sem comentários óbvios. Código autodocumentado.
- Comentários só para explicar "por quê" não-óbvio (constraint, invariante, workaround de bug).
- Testes: Testcontainers para integração, sem mocks de repositório.
- Async por padrão em I/O.
- Não inventar imports. Não criar libs que não existem no `pyproject.toml`.

## Proibições

- **Nunca editar:** `CLAUDE.md`, `AGENTS.md`, `EXECUTION_PLAN.md`, `TASKS.md`, `prometheus-*.md` (specs originais).
- **Nunca acessar** diretórios `work/` ou collections Qdrant `work` sem ctx explícito. Barreira protege código proprietário Avangrid.
- **Nunca commitar** `.env`, credenciais, tokens, dados de cliente.
- **Nunca silenciar** falha de hook ou teste — corrigir causa raiz.
- **Nunca misturar** dados do vault com código do engine.

## Papel do Copilot

Passivo. Completa inline no arquivo que o humano está editando. Não abre branches, não toma decisões de arquitetura, não refatora múltiplos arquivos de uma vez. O trabalho pesado é de Claude Code e Codex; você acelera o boilerplate dentro de um arquivo já em curso.
