# Prometheus — Relatório de Auditoria Documental

**Data:** 2026-04-21  
**Escopo:** Documentação candidate para publicação no Obsidian vault (contextos: knowledge, career, personal, adrs)  
**Status:** Auditoria concluída — ver seção "Plano de Ação"

---

## 1. Matriz de fontes canônicas

| Assunto                             | Fonte canônica                           | Arquivos secondários (não editar como fonte) |
| ----------------------------------- | ---------------------------------------- | -------------------------------------------- |
| Onboarding e arquitetura do sistema | `README.md`                              | `docs/archive/EXECUTION_PLAN.md`             |
| Decisões arquiteturais (ADRs)       | `docs/ADR.md`                            | `docs/archive/prometheus-*.md`               |
| Requisitos arquiteturais            | `docs/ARD.md`                            | `docs/archive/prometheus-context-engine.md`  |
| Operação do CLI e agentes           | `docs/USAGE_GUIDE.md`                    | `README.md` (seção Uso rápido)               |
| Regras de agentes e paths canônicos | `CLAUDE.md`                              | —                                            |
| Estado de tarefas                   | `TASKS.md`                               | —                                            |
| Templates de vault                  | `docs/archive/prometheus-vault-final.md` | —                                            |

---

## 2. Inconsistências encontradas (por prioridade)

### P0 — Bloqueiam publicação ao vault

| ID   | Arquivo               | Linha aprox. | Problema                                                                                                                                                                                                                                 | Ação                                                                                                    |
| ---- | --------------------- | ------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| A-01 | `docs/USAGE_GUIDE.md` | 77–98        | A seção "Padrão Claude + Codex (TDD first)" documenta `codex exec` como se fosse um comando do ecossistema Prometheus. `codex` é uma ferramenta externa, não gerenciada pelo `pb`. Um leitor no vault vai tentar executar isso e falhar. | ✅ Resolvido em 2026-04-21: nota explícita adicionada distinguindo fluxo `pb` de ferramenta externa.    |
| A-02 | `docs/USAGE_GUIDE.md` | 179–198      | Seção "RTK oficial (fim a fim)" mistura o RTK interno do Prometheus com o binário externo `rtk` (instalado via `brew install rtk`). O comando `pb rtk-init --agent codex` existe no código mas depende do binário externo.               | ✅ Resolvido em 2026-04-21: seção separada em RTK interno e RTK externo com pré-requisito explícito.    |
| A-03 | `docs/ARD.md`         | 33–55        | Lista T-010..T-014, T-024, T-025 como "O que ainda falta". `CLAUDE.md` declara "MVP completo (abril 2026)". Contradição — um leitor no vault não sabe se o sistema está funcional ou incompleto.                                         | ✅ Resolvido em 2026-04-21: ARD atualizado para tratar esses itens como baseline operacional histórico. |

### P1 — Degradam qualidade mas não bloqueiam

| ID   | Arquivo               | Linha aprox. | Problema                                                                                                                      | Ação                                                                                                  |
| ---- | --------------------- | ------------ | ----------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| A-04 | `docs/USAGE_GUIDE.md` | 1–20         | Menciona Docker mas não inclui link para `setup.sh` nem ordem de operações completa (setup → pipx → vault bootstrap → index). | ✅ Resolvido em 2026-04-21: referência cruzada para `docs/VAULT_SETUP.md` adicionada no topo do guia. |
| A-05 | `docs/USAGE_GUIDE.md` | 230–260      | Checklist pré-sessão está no final do guia; difícil descobrir.                                                                | ✅ Resolvido em 2026-04-21: referência rápida para o checklist adicionada no topo do guia.            |
| A-06 | `docs/ARD.md`         | —            | Seção "O que ainda falta" não tem data de criação, então parece sempre atual.                                                 | ✅ Resolvido em 2026-04-21: seção convertida para baseline histórico com data e escopo operacional.   |

### P2 — Melhorias de completude

| ID   | Arquivo               | Problema                                                                                                                                                               | Ação                                                                                                        |
| ---- | --------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| A-07 | `docs/USAGE_GUIDE.md` | Sem troubleshooting de infraestrutura (Qdrant down, Ollama OOM, collections não criadas).                                                                              | ✅ Resolvido em 2026-04-21: troubleshooting operacional incorporado em `docs/VAULT_SETUP.md`.               |
| A-08 | Nenhum arquivo        | Não existe guia de bootstrap do vault (`~/vault/`). O único material está em `docs/archive/prometheus-vault-final.md`, não linkado em nenhum lugar do fluxo principal. | ✅ Resolvido em 2026-04-21: `docs/VAULT_SETUP.md` criado e integrado ao guia principal.                     |
| A-09 | `docs/ADR.md`         | ADR formatado sem acentos (encoding ascii). Funcionalmente correto, mas esteticamente inconsistente com o vault em PT-BR.                                              | ✅ Encerrado por decisão em 2026-04-21: mantido em ASCII por política de edição; sem impacto funcional.     |
| A-10 | `README.md`           | Não menciona onde ficam os templates para CONTEXT.md de projetos.                                                                                                      | ✅ Resolvido em 2026-04-21: seção "Documentação para Vault" adicionada com links para setup/mapa/auditoria. |
| A-11 | `README.md`           | Contagem de testes desatualizada: README indicava 139 testes (4 CLI, 17 store) mas suite real era 149 (10 CLI, 19 store, 2 router, 118 embedder).                      | ✅ Resolvido em 2026-04-21: tabela e comentário de testes atualizados para 149 com distribuição correta.    |
| A-12 | `docs/ADR.md`         | ADRs de governança (sensibilidade, owner de policy, fallback, reason_code, SLOs) foram decididos mas não documentados.                                                 | ✅ Resolvido em 2026-04-21: ADR-011 a ADR-015 adicionados com decisões de produto e segurança travadas.     |
| A-13 | `docs/ARD.md`         | Requisitos de budget gate pré-envio, circuit breaker, observabilidade de compliance e retrieval 2-step identificados mas sem ARD correspondente.                       | ✅ Resolvido em 2026-04-21: ARD-007 a ARD-010 adicionados com status de implementação pendente explícito.   |

---

## 3. Validação de comandos documentados vs. implementação

Todos os comandos `pb` citados na `USAGE_GUIDE.md` existem em `src/prometheus/cli/pb.py`:

| Comando                             | Implementado | Observação                                     |
| ----------------------------------- | ------------ | ---------------------------------------------- |
| `pb ask`                            | ✅           |                                                |
| `pb search`                         | ✅           |                                                |
| `pb index`                          | ✅           |                                                |
| `pb watch`                          | ✅           |                                                |
| `pb til`                            | ✅           |                                                |
| `pb til --list`                     | ✅           |                                                |
| `pb til --promote-today`            | ✅           |                                                |
| `pb til howto --from`               | ✅           |                                                |
| `pb adr add / list`                 | ✅           |                                                |
| `pb session <ctx>`                  | ✅           |                                                |
| `pb career brief/interview/metrics` | ✅           |                                                |
| `pb deep suggest`                   | ✅           |                                                |
| `pb cost today/week`                | ✅           |                                                |
| `pb rtk`                            | ✅           | Requer binário externo `rtk` — não nativo      |
| `pb rtk-init`                       | ✅           | Requer binário externo `rtk` — não nativo      |
| `pb rtk-status`                     | ✅           | Requer binário externo `rtk` — não nativo      |
| `pb rtk-proxy`                      | ✅           | Requer binário externo `rtk` — não nativo      |
| `codex exec`                        | ❌           | **Externo ao Prometheus.** Não é comando `pb`. |

---

## 4. Guardrails de contexto

Validação do isolamento `work`:

- `src/prometheus/store/collections.py` — coleção `work` excluída de buscas sem `ctx=work`. ✅
- `src/prometheus/cli/pb.py` — `_resolve_ctx()` aplica barreira. ✅
- `docs/USAGE_GUIDE.md` — aviso explícito em 3 pontos do guia. ✅
- `docs/archive/prometheus-vault-final.md` — tabela de contextos com coluna "Requer confirmação". ✅
- Lacuna: nenhum arquivo descreve o arquivo `.ctxguard` para quem configura o vault pela primeira vez. Coberto em `VAULT_SETUP.md`.

---

## 5. Plano de ação

| Prioridade | Entregável                                                                             | Status     |
| ---------- | -------------------------------------------------------------------------------------- | ---------- |
| P0         | `docs/AUDIT_REPORT.md` (este arquivo)                                                  | ✅ Criado  |
| P1         | `docs/VAULT_SETUP.md` — bootstrap completo do vault                                    | ✅ Criado  |
| P2         | `docs/VAULT_PUBLISHING_MAP.md` — mapa de publicação por contexto                       | ✅ Criado  |
| P0         | Seção de aviso em `docs/USAGE_GUIDE.md` distinguindo `codex exec` de fluxo `pb` nativo | ✅ Editado |
| P0         | Separar RTK interno vs externo em `docs/USAGE_GUIDE.md`                                | ✅ Editado |
| P2         | `README.md` — corrigir contagem de testes para 149 e tabela de suites                  | ✅ Editado |
| P2         | `docs/ADR.md` — adicionar ADR-011..015 (governança, policy, SLOs, fallback)            | ✅ Editado |
| P2         | `docs/ARD.md` — adicionar ARD-007..010 (budget gate, breaker, compliance, retrieval)   | ✅ Editado |

---

## 6. O que NÃO deve ser publicado no vault

| Item                                        | Motivo                                                                                   |
| ------------------------------------------- | ---------------------------------------------------------------------------------------- |
| `docs/archive/*`                            | Specs históricas — reference only. Não publicar como guia operacional.                   |
| `TASKS.md`                                  | Backlog interno do engine. Não relevante para operação do vault.                         |
| `CLAUDE.md`                                 | Contexto de agente técnico, não documento de usuário. Vault tem seu próprio `CLAUDE.md`. |
| `AGENTS.md`                                 | Protocolo interno de agentes.                                                            |
| `conftest.py`, `pyproject.toml`, `setup.sh` | Infraestrutura do engine, não do vault.                                                  |
| `.env`, `.env.local`                        | Credenciais — nunca no vault.                                                            |
