# Prometheus — Cognitive Capability Audit

**Data:** 2026-05-08
**Escopo:** código real em `src/`, `tests/`, `docs/`
**Metodologia:** inspeção estática + grep por 25 capabilities específicas

Legenda: **SIM** = existe em código / **PARCIAL** = implementação incompleta / **NÃO** = ausente

---

## 1. Memória e continuidade cognitiva

| Questão | Veredicto | Evidência |
|---|---|---|
| Diferencia episódica/semântica/procedural/reflexiva? | **NÃO** | Apenas collection-based (personal/career/knowledge/work). Sem taxonomia formal. |
| Recupera "por que" uma decisão foi tomada? | **PARCIAL** | `session_store.py:9-17` — ADR tem campos `rationale` e `why`. Retrieval por "por que" não existe. |
| Decay/priorização por relevância temporal? | **NÃO** | `scoring.py:58-66` tem pesos (relevance 0.35, novelty 0.20, etc.) mas zero TTL ou eviction. |
| Retrieval muda conforme task type? | **NÃO** | Model router roteia por task type. Estratégia de retrieval não muda. |
| Reconstrói contexto após restart? | **PARCIAL** | `.session_state` persiste ctx ativo. Sem re-hidratação de planos ou goals em andamento. |
| Resumização incremental de sessões longas? | **SIM** | `session_compressor.py:40-64` — comprime a cada 10 turns preservando rolling summary + 2 últimos turns. |
| Grafo preserva causalidade entre decisões? | **NÃO** | `graph_store.py` armazena `calls`/`called_by` — dependência estrutural, não causal. |
| Retrieval deterministicamente reproduzível? | **NÃO** | Qdrant com COSINE distance. Não-determinístico entre versões do embedder. |
| Versionamento de memória/contexto? | **NÃO** | `policy_version` e `mem0 v1.1` existem. Chunks e summaries sem versão. |
| Detecta contexto obsoleto? | **NÃO** | `modified_at` existe em chunks. Zero lógica de eviction ou staleness. |

**Placar: 1 SIM / 3 PARCIAL / 6 NÃO**

---

## 2. Retrieval e world model

| Questão | Veredicto | Evidência |
|---|---|---|
| Grafo representa arquitetura ou relações textuais? | **PARCIAL** | Redis = cache de subgrafos estruturais; SQLite = source-of-truth do code graph. Neo4j foi descartado em dec-101. |
| Inferência de impacto indireto entre módulos? | **PARCIAL** | `get_graph_neighbors` tem param `depth`. Multi-hop existe. Inferência de impacto não. |
| Dependency traversal multi-hop? | **SIM** | Param `depth` em `get_graph_neighbors`. |
| Retrieval considera relevância vetorial? | **SIM** | Qdrant COSINE. |
| Retrieval considera dependência estrutural? | **SIM** | Graph store integrado. |
| Retrieval considera recência? | **NÃO** | Ausente. |
| Retrieval considera criticidade? | **NÃO** | `actionability` score existe mas não é criticidade de negócio. |
| Retrieval considera ownership? | **NÃO** | Ausente. |
| Hierarchical retrieval? | **NÃO** | Flat search. |
| "Quais mudanças similares quebraram isso antes?" | **NÃO** | Zero memória de falhas históricas. |
| Namespace isolation verificável? | **PARCIAL** | `is_corporate_context()` impede cloud. Sem audit trail de vazamento. |
| Auto-expand quando confiança baixa? | **NÃO** | Ausente. |
| Retrieval adaptativo por tipo de falha? | **NÃO** | Ausente. |
| Mede qualidade do retrieval? | **PARCIAL** | `scoring.py` pontua candidatos antes de inserir. Sem medição pós-retrieval. |

**Placar: 3 SIM / 4 PARCIAL / 7 NÃO**

---

## 3. Compression e preservação semântica

| Questão | Veredicto | Evidência |
|---|---|---|
| Detecta perda semântica ou apenas tokens/símbolos? | **PARCIAL** | `compression_quality.py:26-42` — preserva symbols (method names). Semântica real não validada. |
| Semantic equivalence validation? | **NÃO** | Symbol-anchor preservation ≠ equivalência semântica. |
| Preserva imports/APIs/contracts/constraints? | **PARCIAL** | Symbols sim. ADR decisions e contracts não explicitamente. |
| Score de confiança da compressão? | **NÃO** | Retorna `(text, error_note)`. Pass/fail binário. |
| Benchmark compressão vs degradação de execução? | **NÃO** | Ausente. |
| Detecta compressão excessiva? | **NÃO** | Ausente. |
| Fallback por nível de criticidade? | **NÃO** | `strict mode` existe mas não é baseado em criticidade. |
| Contextos críticos pulam compressão? | **NÃO** | Ausente. |
| Compressão diferente para código/arquitetura/logs/stack traces? | **PARCIAL** | Código usa symbol preservation. Logs e stack traces sem estratégia distinta. |
| Retrieval escolhe compressed/full dinamicamente? | **NÃO** | Ausente. |

**Placar: 0 SIM / 3 PARCIAL / 7 NÃO**

---

## 4. Reflection e aprendizado

| Questão | Veredicto | Evidência |
|---|---|---|
| Armazena failures ou apenas sucessos? | **PARCIAL** | `circuit_breaker.py:73-87` rastreia failures em memória. Sem persistent failure store para análise. |
| Memória de retries malsucedidos? | **PARCIAL** | Circuit breaker conta. Sem armazenamento pós-sessão. |
| Aprende padrões de erro recorrentes? | **NÃO** | Ausente. |
| Classificação de falhas? | **NÃO** | Erros logados genericamente. |
| Distinção hallucination / retrieval failure / execution failure / planning failure? | **NÃO** | Ausente. |
| Ajusta comportamento após falhas repetidas? | **PARCIAL** | Circuit breaker abre após threshold. Sem adaptação de comportamento real. |
| Confidence decay após retries? | **NÃO** | Ausente. |
| Sabe quando NÃO confiar em memória antiga? | **NÃO** | Ausente. |
| Self-critique pipeline? | **NÃO** | Ausente. |
| Gera "lessons learned" automaticamente? | **NÃO** | `til_promoter.py` promove TILs para HOW-TOs mas TIL é captura manual. |

**Placar: 0 SIM / 3 PARCIAL / 7 NÃO**

---

## 5. Planejamento e raciocínio

| Questão | Veredicto | Evidência |
|---|---|---|
| Armazena planos anteriores? | **NÃO** | `pb plan` gera scaffold mas não persiste. |
| Reusable plan memory? | **NÃO** | Ausente. |
| Recupera workflows similares? | **NÃO** | Semantic search poderia, mas sem schema de planos para buscar. |
| Representação explícita de goals/subgoals? | **NÃO** | `pb plan` tem stages, sem persistência de goal state. |
| Dependency-aware planning support? | **NÃO** | Ausente. |
| Identifica tasks de alto risco? | **NÃO** | Apenas no spec do Odisseu, não no Prometheus. |
| Architecture-aware planning? | **PARCIAL** | ADRs ajudam. Sem enforcement automático de constraints arquiteturais. |
| Detecta scope explosion? | **NÃO** | Ausente. |
| Heurísticas de simplificação? | **PARCIAL** | Regra comportamental no CLAUDE.md. Não estrutural. |
| Favorece mudanças cirúrgicas automaticamente? | **NÃO** | Regra de agente, não mecanismo do sistema. |

**Placar: 0 SIM / 3 PARCIAL / 7 NÃO**

---

## 6. Tool orchestration

| Questão | Veredicto | Evidência |
|---|---|---|
| Sabe quais tools usar por contexto? | **PARCIAL** | Model router sabe por task type. Tool selection implícita. |
| Tool capability registry? | **NÃO** | `SourceRegistry` existe para knowledge sources, não para tools. |
| Policy por tool? | **NÃO** | Policy por contexto, não por ferramenta. |
| Risk scoring por comando? | **NÃO** | Ausente. |
| Detecta comandos destrutivos semanticamente? | **NÃO** | Ausente. |
| Sandbox awareness? | **NÃO** | Ausente. |
| Mede confiabilidade das tools? | **NÃO** | Ausente. |
| Retry policy específica por tool? | **NÃO** | Ausente. |
| Troca estratégia de execução? | **NÃO** | Ausente. |
| Tool usage telemetry? | **PARCIAL** | `compression_telemetry.py` — apenas compressão. |

**Placar: 0 SIM / 2 PARCIAL / 8 NÃO**

---

## 7. Governança e segurança

| Questão | Veredicto | Evidência |
|---|---|---|
| DENY_CORPORATE_CLOUD é auditável? | **SIM** | `policy/core.py:95-107` — `ReasonCode.DENY_CORPORATE_CLOUD` enum ativo. |
| Enforcement real ou apenas convenção? | **SIM** | `runtime.py:159-162` — `is_corporate_context()` bloqueia em código. |
| Tracing de decisões de policy? | **PARCIAL** | `compliance.py:11-26` — `ComplianceEvent` com `decision_id`. Sem full chain. |
| Consegue provar isolamento de contexto? | **PARCIAL** | Enforcement existe. Prova formal ausente. |
| Classificação de sensibilidade de memória? | **PARCIAL** | RESTRICTED / CONFIDENTIAL / PUBLIC definidos. |
| Controle de exfiltration risk? | **NÃO** | Ausente. |
| Allowlist/denylist dinâmica? | **NÃO** | Estática em código. |
| Registra violações? | **PARCIAL** | Compliance logging existe. Sem alertas ou dashboards. |
| Separação trusted/untrusted context? | **PARCIAL** | ctx isolation sim. Trust levels formais não. |
| Policy inheritance por namespace? | **NÃO** | Ausente. |

**Placar: 2 SIM / 6 PARCIAL / 2 NÃO** ← categoria mais forte do sistema

---

## 8. Observabilidade

| Questão | Veredicto | Evidência |
|---|---|---|
| Reconstrói cadeia de decisão completa? | **NÃO** | Ausente. |
| Execution timeline? | **NÃO** | Ausente. |
| Tracing retrieval→prompt→output? | **PARCIAL** | `ComplianceEvent` captura decisão. Sem trace end-to-end. |
| Mede latência? | **NÃO** | Ausente em telemetria. |
| Mede retrieval hit quality? | **PARCIAL** | Scoring pré-retrieval. Sem validação pós-output. |
| Mede retry rate? | **PARCIAL** | Circuit breaker conta. Sem agregação por sessão. |
| Mede hallucination rate? | **NÃO** | Ausente. |
| Mede token efficiency? | **PARCIAL** | `compression_telemetry.py` rastreia calls de compressão. |
| Correlation ID entre sessões? | **PARCIAL** | `decision_id` por decisão. Sem ID cross-session. |
| Replay de execução? | **NÃO** | Ausente. |
| Detecta drift? | **NÃO** | Ausente. |
| Benchmark suite fixa? | **PARCIAL** | D5: chunker quality gate. Sem benchmark de sistema completo. |
| Métricas de sucesso por task type? | **NÃO** | Ausente. |
| Mede qualidade do contexto entregue? | **NÃO** | Ausente. |

**Placar: 0 SIM / 6 PARCIAL / 8 NÃO**

---

## 9. Deep-agent readiness

| Questão | Veredicto | Evidência |
|---|---|---|
| Suporta long-running tasks? | **PARCIAL** | `pb plan` tem 5 stages. Sem persistência entre stages. |
| Checkpointing? | **NÃO** | `.session_state` só preserva ctx ativo. Sem task checkpoint. |
| Resume-from-failure real? | **NÃO** | Ausente. |
| Coerência após múltiplos replans? | **NÃO** | Ausente. |
| Memory consolidation? | **NÃO** | Ausente. |
| Procedural memory persistente? | **PARCIAL** | ADRs capturam decisões de design. Sem procedimentos executáveis. |
| Opera dias no mesmo goal? | **NÃO** | Ausente. |
| Adaptive strategy selection? | **PARCIAL** | Model routing. Sem adaptação baseada em outcomes. |
| Reconhece uncertainty? | **NÃO** | Ausente. |
| Sabe quando pedir ajuda humana? | **NÃO** | `require_ship_approval` é gate fixo, não detecção de incerteza. |

**Placar: 0 SIM / 4 PARCIAL / 6 NÃO**

---

## 10. Pergunta final — a mais importante

> Se todos os modelos fossem substituídos amanhã: o comportamento distintivo do sistema permaneceria, ou a intelligence desapareceria junto com o modelo?

**A intelligence desapareceria junto com o modelo.**

O que sobreviveria à substituição de todos os modelos:

- Memórias armazenadas (TILs, ADRs, session summaries)
- Infraestrutura de retrieval (Qdrant, Redis, SQLite)
- Pipeline de compressão com preservação de símbolo
- Policy enforcement (`DENY_CORPORATE_CLOUD`, ctx isolation)
- Dados do vault

O que desapareceria:

- Julgamento de qual contexto é relevante para um goal
- Planejamento e decomposição de tasks
- Síntese de múltiplas fontes de memória
- Recuperação de erros com raciocínio
- Interpretação de ADRs como restrições ativas

**Diagnóstico:** Prometheus é augmentation infrastructure — eleva a inteligência do modelo via contexto de qualidade. Não é inteligência autônoma. É um exosqueleto cognitivo, não uma mente. O conhecimento persiste; o comportamento não.

---

## Visão geral consolidada

```
Categoria                  Maturidade    Gap crítico
─────────────────────────────────────────────────────────────────────
Governança/segurança       ████████░░   exfiltration control
Memória/continuidade       ████░░░░░░   decay, causalidade, versionamento
Compression                ████░░░░░░   semantic validation, confidence score
Retrieval/world model      ████░░░░░░   recência, criticidade, failure history
Observabilidade            ███░░░░░░░   tracing end-to-end, correlation IDs
Reflection/aprendizado     ██░░░░░░░░   failure classification, self-critique
Planejamento               ██░░░░░░░░   plan reuse, goal persistence
Tool orchestration         █░░░░░░░░░   risk scoring, capability registry
Deep-agent readiness       █░░░░░░░░░   checkpointing, uncertainty detection
```

---

## Gaps prioritários para roadmap

Ordenados por impacto no caminho para deep agent:

| Prioridade | Gap | Onde implementar | Esforço |
|---|---|---|---|
| P0 | Failure memory persistente | `store/failure_store.py` | baixo |
| P0 | Failure classification (hallucination/retrieval/execution) | `observability/` | médio |
| P1 | Checkpointing de goal/task state | `store/agent_store.py` (Odisseu) | médio |
| P1 | Tracing retrieval→prompt→output com correlation ID | `observability/tracer.py` | médio |
| P1 | Compression confidence score | `context/compression_quality.py` | baixo |
| P2 | Memory decay / TTL por recência | `store/vector_store.py` | médio |
| P2 | Retrieval adaptativo por tipo de falha | `store/vector_store.py` | alto |
| P2 | Self-critique pipeline | `memory/` | alto |
| P3 | Causal graph (causa→efeito) | `store/graph_store.py` | alto |
| P3 | Automatic lessons-learned generation | `vault/til_promoter.py` | alto |
