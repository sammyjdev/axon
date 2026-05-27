# dec-110 — Memória declarativa exige sinalização leve no commit

- Status: accepted
- Date: 2026-05-27

## Context

A inferência automática de ADR a partir de cada commit (`pb adr
infer-commit`, implementado em `src/axon/cli/pb.py:1439`) hoje dispara
em todo post-commit hook. ~95% dos commits não são arquiteturais
(bugfixes, deps, formatação, refactor menor) mas o LLM ainda é chamado
e produz JSON. Resultado: ruído no vault, custo de token, e principal
vetor de "ADR alucinado" identificado pelo red-team R1.

Cinco rounds de red-team consideraram: confidence-score (rejeitado,
não calibrado), inferência sempre-ligada com gate (parcial, mas custo
de chamada LLM persiste), handshake explícito tipo `axon snapshot
--adr` (rejeitado, destrói event-driven). Convergiu em sinalização
leve no commit como discriminador.

## Decision

Inferência de ADR só dispara quando o commit contém um dos sinais
explícitos abaixo. Sem sinal, captura derivada (`CodeChange`) persiste
normalmente; inferência de ADR não é executada.

| Sinal | Status | Compatibilidade |
|---|---|---|
| `arch:` subject prefix | **primário** | Conventional Commits via `type-enum` |
| `decision:` subject prefix | sinônimo aceito | idem |
| `ADR-Decision: <título>` trailer no body | **metadado opcional** | sempre compatível |
| `pb adr infer-commit --force` | escape hatch manual | n/a |

O trailer **não é** path canônico — existe para anotação suplementar
que AXON consome se presente, mas nenhum outro tooling precisa
entender. Isso evita conflito com `commitlint` `type-enum` rígido e
parsers de `semantic-release`.

## Rationale

- **Subject prefix `arch:` é Conventional-Commits-friendly**: integra
  ao ecossistema via configuração trivial (`'type-enum': [2, 'always',
  [..., 'arch', 'decision']]`).
- **Trailer fora do path canônico**: usuários com `commitlint`/
  `semantic-release` rígidos podem usar trailer sem quebrar pipeline;
  AXON parseia silenciosamente.
- **Sinal substitui inferência probabilística**: dev sinaliza
  explicitamente quando há decisão arquitetural. Reduz tanto custo de
  chamada LLM quanto superfície de alucinação.
- **Custo de 5–10 caracteres por commit arquitetural** é trivial
  comparado a custo de revisão de drafts ruidosos.

## Consequences

- `pb adr infer-commit` recebe `axon.adr.signal.detect()` no início e
  retorna early se ausente.
- `pb commit` helper opcional pode ser adicionado depois para sugerir
  prefix com base em diff stats.
- Documentar em `docs/USAGE_GUIDE.md` que captura declarativa requer
  sinal.
- Documentar em [dec-114](dec-114-doctor-diagnostic-first.md) que
  `pb doctor` valida compatibilidade com toolchain de commit.
- Dev pode esquecer prefix — workaround: `pb adr add` direto sempre
  disponível. Aceito como risco residual.
