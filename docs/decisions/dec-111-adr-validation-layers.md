# dec-111 — Validação L1-L3 com tiers, densidade e draft pool dormente

- Status: accepted
- Date: 2026-05-27

## Context

A inferência de ADR via LLM (`src/axon/cli/pb.py:1439`) hoje persiste
diretamente no `SessionStore` sem nenhum gate de validação. Red-team
R1 identificou isso como vetor de "ADR alucinado": LLM pode gerar
ADR sintaticamente válido sobre decisão inexistente, e o vault aceita.

Iterações sucessivas convergiram em validação em camadas determinística,
não probabilística (confidence-score de LLM não é calibrada — R2),
considerando `diff ∪ commit_msg_body` para cobrir ADRs abstratos
(R3), com gate de densidade anti-boilerplate (R4), L1 em dois tiers
para preservar SLA do hook (R4), e detector estrutural para evitar
falsos-negativos em refator topológico (R5).

## Decision

ADR inferido passa por camadas determinísticas. Falha em qualquer →
draft pool dormente em `.axon/adr-draft/` (não-indexado, recuperável),
não vai para vault.

### Camadas de validação

| Camada | Onde roda | Check | Pool considerado |
|---|---|---|---|
| **L1-light** | hook (SLA <100ms) | `git cat-file` para arquivos, `git grep` para símbolos no working tree pós-commit | working tree |
| **L1-full** | background / `pb adr review` | grafo tree-sitter (via `axon.code`) | índice quente |
| **L2 lexical** | hook | overlap rationale × tokens ≥ N (default 3 não-stopword) | `diff ∪ commit_msg_body`, após denylist |
| **L3 polaridade** | hook | termos-chave do ADR têm grep-match | idem |
| **Densidade** | hook | denylist boilerplate + architectural lexicon não-no-diff + overlap ratio cap 0.7 | idem |
| **Detector estrutural** | hook | renames/moves relaxam gates de densidade | n/a |
| **L4 humano** | opt-in batch | `pb adr review` confirma drafts | n/a |

### L1 em dois tiers

Hook **sempre** usa L1-light. Contribuição máxima ao hook: <100ms.

Background revalida com L1-full em três triggers determinísticos
(sem daemon — dec-112 proíbe):

1. Hook `post-merge` / `post-checkout` (escopo do `pb hooks install`)
2. Próximo `pb capture-*` ou `pb adr infer-commit` (amortizado)
3. `pb doctor` (manual)

TTL hard de 24h: drafts sem L1-full revalidação → estado
`stale-pending`, reportado por doctor.

L1-full pode **promover** (válido), **rebaixar para dormant** (símbolo
não existe nem no índice quente), ou **manter draft** (indeterminado).

### Gate de densidade

Três checks combinados, anti-boilerplate:

1. **Denylist**: tokens em conjunto fixo (`JIRA-*`, `#\d+`,
   `Co-authored-by`, `Signed-off-by`, types de conventional commit)
   não contam para overlap.
2. **Architectural lexicon hit**: rationale deve conter ≥ 1 token do
   lexicon (`migrate|replace|adopt|introduce|deprecate|refactor|
   pattern|layer|interface|contract|dependency|invariant|...`) que
   **não** está no diff. Prova comentário genuíno, não paráfrase.
3. **Overlap ratio cap**: rejeita se >70% dos tokens do rationale são
   literal substring do diff (LLM copy-paste).

Lexicon inicial: `axon/data/architectural_lexicon.txt`, ~30 termos.
Expansível via `axon.toml#adr.lexicon_path`.

### Detector estrutural

Commit é classificado `structural` se qualquer:

- `git diff --find-renames=80% --name-status` reporta ≥ 2 renames
- ≥ 3 arquivos novos em diretórios não-existentes
- ≥ 2 diretórios renomeados/movidos
- Diff é >90% mudanças de path

Em modo structural, gates de densidade relaxam:

| Gate | Default | Structural |
|---|---|---|
| `overlap_ratio_cap` | 0.7 | 0.9 |
| Architectural lexicon fora do diff | obrigatório | dispensado |
| L2 min overlap | 3 | 2 |

Audit log registra `structural_mode: true` para auditoria pós-fato.

### Draft pool

- Drafts em `.axon/adr-draft/{commit_hash}.md`
- Após 30 dias (configurável): marcados `dormant`, fora do retrieval
  default, recuperáveis via `pb adr review --dormant`
- **Não expiram destrutivamente** — preservam memória institucional

### Observabilidade

- Toda rejeição → `.axon/adr-rejected.jsonl`:
  `{commit_hash, layer, reason, tokens_missing?, file_missing?,
  density_score?, structural_mode?, ts}`
- Passes com densidade abaixo de threshold (mas acima de rejeição) →
  registrados como **weak-pass** no mesmo log
- `pb adr audit [--since=7d] [--weak-passes]` lista candidatos
- Thresholds configuráveis em `axon.toml#adr.*`

### SLA do hook

<500ms p99 total (L1-light + L2 + L3 + densidade + write para
pending). Excedeu → fallback: captura derivada apenas, ADR vai para
pending sem validação L1, revalidado depois por background.

## Rationale

- **Validação estrutural sozinha não captura inversão causal** —
  precisa camadas lexical + polaridade.
- **Pool `diff ∪ commit_msg_body`** — ADRs abstratos têm rationale
  conceitual cujo diff é só imports; commit body é parte legítima do
  sinal arquitetural.
- **Densidade contra boilerplate** — sem ela, copy-paste de diff no
  body passa L2/L3 trivialmente.
- **L1 em tiers preserva SLA** — tree-sitter graph é caro; git-only é
  rápido e suficiente para hook path. Precisão final via background.
- **Detector estrutural** — refator topológico (renomear diretório
  para quebrar acoplamento) tem rationale ≈ diff por natureza;
  rejeitar seria falso-negativo crítico.
- **Draft dormente** — não expirar destrutivamente preserva memória
  institucional contra pressão de sprint.

## Consequences

- Novo módulo `axon.adr.gates` com submódulos `l1`, `l2`, `l3`,
  `density`, `structural`.
- Novo módulo `axon.adr.draft_pool` para escrita/dormency em
  `.axon/adr-draft/`.
- Novo recurso `axon/data/architectural_lexicon.txt`.
- `adr_infer_commit` (`pb.py:1439`) refatorado para orquestrar:
  signal → infer → L1-light → L2 → L3 → density → structural → draft
  pool ou SessionStore.
- Novas CLIs:
  - `pb adr review [--dormant] [--weak-passes]`
  - `pb adr audit [--since=7d]`
  - `pb adr validate-drafts` (chamado por triggers)
- Hooks `post-merge` e `post-checkout` adicionados ao escopo do
  `pb hooks install` ([dec-113](dec-113-hooks-pre-commit-framework.md)).
- `pb doctor` reporta drafts `stale-pending` (TTL excedido) —
  [dec-114](dec-114-doctor-diagnostic-first.md).
- Aceito como risco residual: alucinação que passa L1-light + L2/L3 +
  densidade (baixa probabilidade); rebaixada por L1-full depois.
- Aceito como risco residual: L1-full pode rebaixar draft horas depois
  do hook; usuário pode ler vault entre os dois pontos.
- Aceito como risco residual: lexicon inicial pode rejeitar ADRs
  válidos — configurável, evolui por feedback.
