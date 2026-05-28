# AXON — Robustez da Captura de Contexto e Memória

- Status: proposta consolidada, round 5 (CONVERGINDO → cristalizada)
- Date: 2026-05-27
- Escopo: política de persistência, validação de ADR inferido, concorrência
  de escrita, integração com toolchain de hooks
- Materializado em: `dec-110..114` (`docs/decisions/`)

## 1. Propósito

Consolidar, em um único documento, o design defendido após cinco rounds
de red-team (`grill-with-docs`) sobre a camada de captura de contexto
do AXON. Cristaliza as 5 decisões (`dec-110..114`) com todos os deltas
absorvidos, e serve como referência única para implementação.

## 2. Nível de Robustez Alvo

- **Perfil de uso primário**: solo-dev e small-team (2–10 devs).
- **Tolerância a risco residual conhecido**: alta, desde que
  documentado e com workaround.
- **Não-alvo nesta fase**: compliance enterprise B2B (SOC2/ISO27001).
- **Critério de aceitação**: para uma crítica derrubar este design, ela
  precisa apontar fragilidade com probabilidade > 5% no horizonte de 12
  meses **e** custo de mitigação < custo de falha em razão > 3:1.
- **Convergência aceitável**: 3–5 fragilidades residuais documentadas
  com workaround conhecido. Não buscamos 99%.

## 3. Restrições Herdadas (não relitigar)

| Origem | Restrição |
|---|---|
| `dec-103` | MCP é transport primário; `.axon/context.md` é fallback (view derivada — ver dec-112) |
| `dec-104` | Captura é exclusivamente event-driven (sem polling) |
| `dec-109` | Tool risk classification + policy gate aplicam-se a writes em SessionStore |
| `ADR-001` | Data e engine ficam em paths separados |
| `ADR-010` / `ARD-007` | Compressão é rejeitada se perde anchors |
| `ADR-011` | AXON é engine de memória/contexto/governança, não runtime de agente |
| `dec-106` | Profile system FREE/PAID com Ollama opt-in |

## 4. Tipologia da Memória (fundação conceitual)

| Tipo | Exemplos | Recomputável | Hallucinável | Custo se errado | Trigger |
|---|---|---|---|---|---|
| **Derivada** | `CodeChange`, chunks, `SessionMemory` summary | sim | parcial | baixo | automático no evento |
| **Declarativa** | `ADR`, decisão arquitetural | **não** | **sim** | **alto** | sinal explícito + gate |
| **Manual** | `SessionNote`, `pb adr add` direto | n/a | não | baixo | gesto humano |

Apenas memória **declarativa** justifica gate. Derivada e manual seguem
o caminho atual.

## 5. As 5 Decisões

Sumário; corpo completo em `docs/decisions/dec-110..114-*.md`.

### dec-110 — Memória declarativa exige sinalização leve no commit

Inferência de ADR só dispara quando o commit contém um dos sinais
explícitos:

| Sinal | Status |
|---|---|
| `arch:` / `decision:` subject prefix | primário |
| `ADR-Decision: <título>` trailer | metadado opcional |
| `pb adr infer-commit --force` | escape hatch |

Sem sinal, captura derivada persiste; ADR não é inferido. Convenção
trivial (5–10 caracteres) elimina ~95% da poluição do vault.

### dec-111 — Validação L1-L3 com tiers + densidade + draft dormente

ADR inferido passa por camadas determinísticas. Falha em qualquer →
draft pool dormente.

| Camada | Onde roda | O que checa |
|---|---|---|
| L1-light | hook (SLA <100ms) | git cat-file + git grep no working tree |
| L1-full | background / `pb adr review` | grafo tree-sitter |
| L2 lexical | hook | overlap rationale × (`diff ∪ commit_msg_body`) |
| L3 polaridade | hook | grep-match de termos-chave |
| Densidade | hook | denylist boilerplate + architectural lexicon + overlap ratio cap |
| Detector estrutural | hook | renames/moves relaxam gates de densidade |
| L4 humano | opt-in | `pb adr review` em batch |

Drafts não expiram destrutivamente (30d dormency). TTL hard de 24h
sem L1-full revalidação → estado `stale-pending`. SLA do hook total:
<500ms p99.

### dec-112 — SQLite WAL + pending dir + drain idempotente

`SessionStore` aplica `PRAGMA journal_mode=WAL; busy_timeout=5000;
synchronous=NORMAL`. Sob `SQLITE_BUSY`: retry com backoff+jitter,
budget 10s, depois escreve em `.axon/pending/{commit_hash}-{ts_ns}.json`
e retorna sucesso ao hook (nunca quebra git).

Drain (acionado por próximo `pb capture-*`, hook `post-merge`/
`post-checkout`, ou `pb doctor`):
- Processa em ordem cronológica via stat
- try/except por arquivo
- Malformados → `.axon/pending-quarantine/` com log em `quarantine.jsonl`
- Loop continua, sem bloquear backlog

Sem daemon, sem socket, sem fila externa, sem Postgres opcional.

### dec-113 — Hooks via pre-commit framework, opt-in com `--apply`

AXON não muta `git config core.hooksPath`. Não escreve em `.git/hooks/`
por padrão. `pb hooks install` é gesto explícito: detecta toolchain,
mostra dry-run, exige `--apply`. Integra como entry em
`.pre-commit-config.yaml` quando o framework existe.

### dec-114 — `pb doctor` diagnóstico-first + validação de toolchain

Três modos: default (read-only), `--apply` (interativo, nunca em CI),
`--ci` (JSON, exit 0 sempre).

Checks adicionais: backlog `pending/`, drafts `stale-pending`, tamanho
do `pending-quarantine/`, warnings persistentes, compatibilidade
`arch:` com `commitlint`/`semantic-release`.

## 6. Riscos Residuais Aceitos

| Risco | P(12m) | Mitigação | Por que aceitamos |
|---|---|---|---|
| Drafts dormentes acumulam disco | alta | `pb adr review --purge-dormant` | Custo trivial vs perda de memória |
| Dev esquece `arch:` em commit arquitetural | media | `pb adr add` direto | Trade-off ruído vs fricção |
| WAL/rename em FS exótico | baixa | documentado em SUPPORT_MATRIX | Não vale para 1% dos paths |
| Pending acumula se SessionStore down | baixa | doctor reporta backlog | Self-healing no próximo drain |
| Husky ausente | media | mensagem clara + docs | Pre-commit é padrão amplo |
| Alucinação passa L1-light + L2/L3 + densidade | baixa | L1-full em background rebaixa | Custo = entrada de draft + rebaixamento |
| L1-full rebaixa draft horas depois | media | log audit + UI mostra estado | Trade-off SLA hook vs precisão |
| Lexicon incompleto rejeita ADR válido | media | configurável; expansão por feedback | Default enxuto, evolui |
| Toolchain custom ignora `arch:` | media | doctor warning + trailer alternativo | Detectável e documentado |
| Retrabalho se enterprise B2B exigir auditoria | media | escopo separado | Não-alvo desta fase |

## 7. Fora de Escopo

- Compressão semântica (`ADR-010`, `ARD-007`, P2-T6)
- Routing de modelos (`dec-106`)
- Distribuição cross-platform (`dec-107`, P0)
- Hardening B2B / SOC2 / data residency
- Benchmark adversarial de compressão (P3-T4 cobre baseline)

## 8. Pontos Abertos Para Validação Final

1. Threshold lexical L2 (default 3): calibrar com dataset pequeno.
2. Janela de dormency (default 30d): configurável.
3. Retry budget (default 10s): medir em smoke test.
4. `pb hooks install` em CI não-interativo: exit 1, nunca mutar.
5. Rotação de `adr-rejected.jsonl`: cap 10MB com rotação por idade.
6. Lexicon inicial: ~30 termos; revisar após 3 meses de uso real.
7. SLA do hook 500ms p99: medir em repos grandes.
8. **Density `overlap_ratio_cap` calibrado de 0.7 → 0.85 (default) e
   0.9 → 0.95 (structural) em 2026-05-28** após 4 runs NIM 8B contra
   commits `arch:` reais. Cap antigo lutava contra prompt novo que
   demanda citação concreta de identificadores do diff.

## 9. Plano de Incorporação (referência para issues)

| Fase | Decs | Esforço | Bloqueia |
|---|---|---|---|
| 0 — Docs | todos | ~1h | tudo |
| 1 — Fundação SQLite/pending | dec-112 | 3-4d | Fase 2-4 |
| 2 — Captura declarativa | dec-110 + dec-111 | 10-12d | Fase 4 |
| 3 — Hooks | dec-113 | 3-4d | (paralelo) |
| 4 — Doctor | dec-114 | 2-3d | depende 1+2+3 |

Total: ~3 semanas com 1 dev, ~2 semanas com 2 devs (Fase 3 destacada).

## 10. Critério de Aprovação

- [x] Red-team R5 confirma `CONVERGINDO` com riscos residuais (§6)
      aceitáveis para o nível declarado (§2)
- [x] Nenhuma fragilidade com razão impacto/mitigação > 3:1 sem
      decisão explícita
- [x] Os 5 dec-* materializados em `docs/decisions/dec-110..114-*.md`
- [x] `docs/ADR.md` recebe entradas correspondentes
- [ ] Issues de implementação abertas (uma por dec-*)
- [ ] Round 6 declara `CONVERGIU` (opcional)

## Apêndice A — Mapeamento de ataques absorvidos

| Round | Ataque | Resolução |
|---|---|---|
| R1 | Hooks contornados por `--no-verify` | dec-113 + file fallback |
| R1 | SQLite lock contention multi-agent | dec-112: WAL + busy_timeout |
| R1 | Local-first vs cloud providers | dec-106 (herdado) |
| R1 | Ecossistema absorve MCP nativamente | ADR-011 (herdado) |
| R1 | Compressão perde semântica | ADR-010 + ARD-007 (herdado) |
| R1 | ADR LLM alucinado | dec-111: L1-L3 + draft pool |
| R1 | `pb doctor --repair` em CI mutila husky | dec-114: `--apply` opt-in |
| R2 | `core.hooksPath` usurpa husky | dec-113: nunca muta |
| R2 | Unix socket quebra Windows nativo | dec-112: daemon eliminado |
| R2 | Validação estrutural só sintática | dec-111: camadas |
| R2 | Confidence-score não calibrado | dec-111: gates determinísticos |
| R2 | Classificação derivada/declarativa ambígua | dec-110: tipo por método |
| R2 | Draft pool 7d mata memória válida | dec-111: dormente 30d |
| R3 | L2/L3 silent-loss em ADR abstrato | dec-111: pool `diff ∪ commit_msg`; audit log |
| R3 | Retry SQLite fixo sem fallback | dec-112: pending dir, drain idempotente |
| R3 | Trailer `ADR-Decision:` quebra commitlint | dec-110: `arch:` primário; dec-114: doctor valida |
| R4 | Corrida no fallback file (sem flock) | dec-112: pending dir com paths únicos |
| R4 | Diluição de sinal por boilerplate | dec-111: gate de densidade |
| R4 | Latência/estaleira do grafo L1 no hook | dec-111: L1 em dois tiers |
| R5 | Trigger determinístico para L1-full ausente | dec-111: post-merge/post-checkout + amortização + TTL 24h |
| R5 | Drain sem isolamento de erro | dec-112: quarantine pattern |
| R5 | Falsos-negativos em refator estrutural | dec-111: detector estrutural relaxa gates |
