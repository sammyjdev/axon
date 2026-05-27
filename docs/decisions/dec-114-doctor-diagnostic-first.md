# dec-114 — `pb doctor` diagnóstico-first + validação de toolchain

- Status: accepted
- Date: 2026-05-27

## Context

`pb doctor` (`src/axon/cli/pb.py:685`) hoje é check único, sem modos.
ARD-010 já define hardware-fit detection. P0-T4 do roadmap pede
`pb doctor` para emitir pass/warn/fail e recomendar modo de operação.

Red-team R1 propôs `pb doctor --repair` para corrigir hooks
divergentes. **Rejeitado** porque mutação automática em
`.git/hooks/` ou outras superfícies compartilhadas é classificada
como risco em qualquer revisão de ferramenta enterprise. R3 pediu
validação de compatibilidade com toolchain de commit (`commitlint`,
`semantic-release`) para o sinal `arch:` ([dec-110](dec-110-declarative-memory-signal.md)).
R4-R5 pediram checks para backlog de `pending/`, drafts
`stale-pending`, e tamanho do `pending-quarantine/`.

## Decision

### Três modos

| Modo | Default? | Comportamento |
|---|---|---|
| `pb doctor` | sim | diagnóstico read-only, exit code reflete severidade |
| `pb doctor --apply` | opt-in | sugere correções com confirmação interativa; **nunca** em CI |
| `pb doctor --ci` | explícito | output JSON em stdout, exit 0 sempre |

`--apply` requer TTY check; refuse com exit 1 em non-interactive.

### Checks obrigatórios

| Check | Categoria | Detalhe |
|---|---|---|
| Hooks divergentes do esperado | hooks | sem reparar (dec-113) |
| Dependências runtime | env | Python ≥ 3.11, SQLite WAL viability |
| Hardware fit | env | reusa ARD-010 |
| Backlog em `.axon/pending/` | captura | warning se > N arquivos ou > T dias |
| Drafts em `stale-pending` (TTL excedido) | ADR | drafts sem L1-full após 24h |
| Tamanho de `.axon/pending-quarantine/` | captura | warning + listagem |
| Warnings persistentes em `capture-warnings.jsonl` | captura | sinal de contenção crônica |
| Compatibilidade `arch:` com toolchain de commit | hooks | scan de `commitlint.config.*`, `.commitlintrc*`, `release.config.js`, `package.json#commitlint`; warning + snippet de fix se `type-enum` rígido sem `arch`/`decision` |

### O que doctor NÃO faz

- Não muta husky ou pre-commit do usuário
- Não corrige `commitlint.config` automaticamente — só sugere
- Não reinstala hooks AXON automaticamente — só reporta divergência
- Não apaga drafts dormentes — só reporta acumulação
- Não apaga quarantine — só reporta

### Output

**Default mode**: tabela human-readable com colunas
`check | status | detalhe | sugestão`. Exit code reflete severidade
máxima (0=ok, 1=warn, 2=fail).

**CI mode**: JSON estruturado:

```json
{
  "version": "1",
  "ts": "...",
  "checks": [
    {"name": "...", "status": "ok|warn|fail", "detail": "...",
     "suggestion": "..."}
  ],
  "summary": {"ok": N, "warn": N, "fail": N}
}
```

Exit 0 sempre em `--ci` para não quebrar pipelines.

**Apply mode**: por check com `auto_fix` disponível (raro), prompt
interativo `[y/N]`. Sem auto_fix → mesmo output do default.

## Rationale

- **Doctor mutador é risco** em qualquer security review enterprise.
  Diagnóstico + `--apply` opt-in preserva valor sem cruzar a linha.
- **CI mode exit 0** evita que doctor vire blocker de pipeline por
  warnings; usuário decide quando agir.
- **Validação de toolchain de commit** previne falha em produção:
  dev configura `arch:`, type-enum rejeita, pipeline quebra. Doctor
  detecta antes do primeiro uso.
- **Checks de backlog/quarantine** dão visibilidade ao estado da
  captura sem inspeção manual do `.axon/`.

## Consequences

- `pb doctor` refatorado em `pb.py:685` para 3 modos.
- Novo módulo `axon.doctor` com `checks/` por categoria
  (hooks, env, capture, adr, toolchain).
- Cada check expõe `(status, detail, suggestion, auto_fix?)`.
- Output formatadores separados (`formatters/human.py`,
  `formatters/json.py`).
- `--ci` mode usado por CI workflows (referenciado em dec-107).
- Aceito como risco residual: usuário ignora warnings persistentes —
  doctor não força ação, apenas reporta.
- Aceito como risco residual: toolchain custom de commit que não
  segue convenção `commitlint`/`semantic-release` não é detectada —
  workaround manual via trailer (dec-110).
