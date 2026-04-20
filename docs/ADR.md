# Prometheus - ADR (Architecture Decision Records)

Data de consolidacao: 2026-04-19
Status: Ativo (documento canonico de decisoes)

## Objetivo

Este documento concentra as decisoes arquiteturais oficiais do projeto.

Historico detalhado de planejamento e specs antigas em `docs/archive/`.

## Fontes arquivadas

- docs/archive/EXECUTION_PLAN.md
- docs/archive/prometheus-context-detection-crossplatform.md
- docs/archive/prometheus-context-engine.md
- docs/archive/prometheus-context-isolation.md
- docs/archive/prometheus-knowledge-split.md
- docs/archive/prometheus-second-brain-full.md
- docs/archive/prometheus-vault-final.md

---

## ADR-001 - Paths canonicos

- Decisao: separar dados e engine por path fixo.
- Definicao: `PROMETHEUS_VAULT=~/vault` e `PROMETHEUS_ENGINE=/Users/samdev/dev/Prometheus`.
- Motivo: evitar mistura de conteudo e reduzir risco de vazamento de contexto.

## ADR-002 - Roteamento de modelos Anthropic por tipo de tarefa

- Decisao: usar roteamento por classe de tarefa com fallback.
- Definicao:
  - TRIVIAL_COMPLETION -> `claude-haiku-4-5-20251001`
  - CODE_ANALYSIS -> `claude-sonnet-4-6`
  - ARCHITECTURE/DEEP_REASONING -> `claude-opus-4-7`
  - Fallback -> `claude-haiku-4-5-20251001`
- Motivo: equilibrar custo e qualidade mantendo previsibilidade.

## ADR-003 - Modelos locais Ollama

- Decisao: padronizar em `gemma4:e4b`, `gemma4:26b` e `phi3:mini`.
- Motivo: reduzir custo cloud e manter baixa latencia local.

## ADR-004 - Backend de grafo separado por responsabilidade

- Decisao: Redis para dependencias de codigo; Neo4j apenas para Mem0.
- Motivo: separar grafo operacional de codigo do grafo semantico de memoria.

## ADR-005 - Chunker Java como gate de qualidade

- Decisao: desenvolvimento TDD-first com suite de fixtures Spring antes do restante do pipeline.
- Criterio: 30+ fixtures e suite 100% verde antes de promover alteracoes.
- Motivo: chunking correto e pre-requisito para recuperacao de contexto confiavel.

## ADR-006 - Barreira de contexto work

- Decisao: work nao participa de busca sem explicitude de contexto.
- Mecanismo:
  - collections separadas por contexto;
  - bloqueio em MCP e CLI;
  - confirmacao explicita para acesso.
- Motivo: protecao de propriedade intelectual e isolamento de dados sensiveis.

## ADR-007 - Arquitetura em 5 camadas

- Decisao: Watcher -> Embedder -> Store -> Router -> MCP Server.
- Motivo: desacoplamento, observabilidade e evolucao incremental por camada.

## ADR-008 - Stack local principal

- Decisao: usar Docker Compose com Qdrant, Redis, Neo4j, Postgres, Langfuse e Ollama (profiles cpu/gpu).
- Motivo: reproducibilidade local em Mac e PC com minimizacao de custo.

## ADR-009 - Knowledge split

- Decisao: dividir conhecimento em knowledge/daily (captura rapida/TIL) e knowledge/deep (estudo acumulativo).
- Motivo: preservar fluxo diario sem perder consolidacao de aprendizado.

## ADR-010 - Memoria de sessao comprimida

- Decisao: compactor periodico de sessao + hook de fim de sessao para resumo no daily note.
- Motivo: reduzir tokens e manter continuidade entre sessoes.

---

## Politica de manutencao

- Novas decisoes arquiteturais entram como novos ADRs neste arquivo.
- Nao usar `docs/archive/` como fonte ativa de decisao.
