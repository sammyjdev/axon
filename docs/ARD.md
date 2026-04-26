# Prometheus - ARD (Architecture Requirement Document)

Data de consolidacao: 2026-04-19
Status: Ativo (documento canonico de requisitos)

## Objetivo

Este documento concentra os requisitos arquiteturais obrigatorios do projeto.

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

## ARD-001 - Isolamento de contexto

- O sistema deve excluir work por padrao em buscas sem `ctx=work`.
- O sistema deve exigir sinalizacao explicita para acesso a contexto restrito.

## ARD-002 - Rastreabilidade e memoria

- ADRs devem ser persistidos em store local.
- Session memory deve manter historico resumido para continuidade de raciocinio.

## ARD-003 - Qualidade de chunking

- O chunker deve operar por estrutura de linguagem (AST/tree-sitter), nao por arquivo bruto.
- A suite de fixtures deve permanecer verde para aceitar mudancas no chunker.

## ARD-004 - Custo e budget

- O router deve aplicar budget gate e fallback de modelo quando exceder limite diario.

## ARD-005 - Operacao cross-platform

- Setup e configuracao devem funcionar em Mac e Linux/PC com alternancia cpu/gpu.

## ARD-006 - Observabilidade minima

- O ambiente deve suportar monitoracao de custo/uso (Langfuse) e logs dos servicos principais.

## ARD-007 - Budget gate final pre-envio

- O router deve validar o custo estimado dos tokens montados apos compressao RTK e antes do envio ao provedor.
- Nenhuma chamada ao provedor deve ocorrer quando o custo estimado supera o budget restante do dia.
- Motivo: evitar estouro silencioso de budget em requests pesados pos-compressao.
- Status: requisito identificado (2026-04-21); implementacao pendente.

## ARD-008 - Circuit breaker distribuido por provider:model

- O sistema deve manter estado de circuit breaker persistido em Redis por chave `breaker:<provider>:<model>`.
- Transicoes de estado (CLOSED -> OPEN -> HALF_OPEN -> CLOSED) devem ser atomicas entre workers.
- Retry com jitter exponencial e half-open controlado (max N tentativas antes de fechar ou reabrir).
- Motivo: evitar cascata de falhas em provedores instáveis sem bloquear rotas alternativas.
- Status: requisito identificado (2026-04-21); implementacao pendente.

## ARD-009 - Observabilidade de compliance sem conteudo sensivel

- Toda decisao de roteamento deve emitir evento com: decision_id, reason_code, policy_version, route, ctx.
- Os eventos nao devem conter conteudo da mensagem, tokens ou dados do usuario.
- Motivo: auditabilidade completa para compliance sem risco de vazamento de dados.
- Status: requisito identificado (2026-04-21); implementacao pendente.

## ARD-010 - Retrieval 2-step budget-aware

- A busca vetorial deve suportar parametros: top_k, max_depth, max_nodes, max_tokens.
- O grafo de dependencias deve suportar travessia multi-hop com controle de profundidade e orcamento.
- Motivo: evitar recuperar contexto excessivo que supera budget ou piora qualidade do retrieval.
- Status: requisito identificado (2026-04-21); implementacao pendente.

---

## Estado atual (2026-04-21)

### Entregue

- Fases 1 a 7 implementadas no codigo do engine e consolidadas em master.
- Suite principal validada:
  - tests/embedder/test_chunker_java.py: 118 passed (Java + Python + TypeScript)
  - tests/store/test_stores.py: 19 passed
  - tests/cli/test_pb_cli.py: 10 passed
  - tests/router/test_router.py: 2 passed
  - Total: 149 passed

### Requisitos pendentes de implementacao

ARD-007, ARD-008, ARD-009, ARD-010 sao requisitos identificados e priorizados.
Decisoes de produto e governanca registradas em ADR-011 a ADR-015.
Implementacao entra como proximas tasks em TASKS.md.

### Baseline operacional historico (2026-04-19)

Os itens abaixo eram checklist de bootstrap/operacao manual na data de consolidacao.
Nao representam gap de implementacao do engine.
Para execucao atual, usar `docs/VAULT_SETUP.md`.

Itens mapeados naquele baseline:

1. Vault bootstrap manual

- T-010: criar estrutura do vault
- T-011: escrever CLAUDE.md global do vault
- T-012: escrever .ctx e .ctxguard
- T-013: preencher templates CONTEXT.md
- T-014: git init e commit inicial no vault

2. Operacao de infraestrutura e modelos

- T-024: subir stack completa e validar servicos
- T-025: pull dos modelos Ollama conforme ambiente

3. Integracao de uso

- T-062: registrar MCP no Claude Code e executar smoke test
- T-081: instalar pb como entry-point (pipx)

4. Higiene de produto

- Alinhar referencias antigas de modelos nas specs arquivadas para historico, sem impacto no runtime.

---

## Linha de corte historica para MVP operacional completo

1. Finalizar T-024 e T-025 (stack + modelos)
2. Finalizar T-062 (MCP registrado e testado)
3. Finalizar T-081 (pb instalavel por pipx)
4. Finalizar T-010..T-014 (vault pronto para uso diario)

---

## Politica de manutencao

- Novos requisitos obrigatorios entram como novos ARDs neste arquivo.
- Nao usar `docs/archive/` como fonte ativa de requisitos.
