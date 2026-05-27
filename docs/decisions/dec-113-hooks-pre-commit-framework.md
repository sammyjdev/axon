# dec-113 — Hooks via pre-commit framework, opt-in com `--apply`

- Status: accepted
- Date: 2026-05-27

## Context

`src/axon/hooks/git_installer.py` hoje escreve diretamente em
`.git/hooks/post-commit` e `pre-push`, anexando bloco AXON entre
marcadores `# >>> AXON git hook >>>` / `# <<< AXON git hook <<<`.
Hooks falham silenciosamente (`|| true`), respeitando dec-104.

Red-team R1 questionou hooks como contrato (contornáveis por
`--no-verify`). R2 propôs `core.hooksPath .axon/hooks` como
isolamento; **rejeitado** porque `core.hooksPath` é global ao repo e
**silenciosamente desativa `.git/hooks/`** — usurpa husky e qualquer
ferramenta que escreva no diretório default. Não é coexistência.

Convergiu em: AXON entra como cidadão do ecossistema de hooks
existente, nunca como dono.

## Decision

### Princípios duros

- AXON **não** muta `git config core.hooksPath` em nenhuma circunstância
- AXON **não** escreve em `.git/hooks/` por padrão
- Toda instalação de hook é gesto explícito do usuário com `--apply`
- Diagnóstico (`pb doctor`) é read-only por padrão
  ([dec-114](dec-114-doctor-diagnostic-first.md))

### `pb hooks install` (substitui `pb adr hook-install`)

Comportamento:

1. Detecta toolchain de hooks presente:
   - `pre-commit` framework (`.pre-commit-config.yaml`)
   - `husky` (`.husky/` ou `package.json#husky`)
   - Nenhum (`.git/hooks/` vazio ou só samples)
2. Mostra dry-run: exatamente o que vai escrever, onde
3. Exige `--apply` para mutar

### Integração por toolchain

| Toolchain detectada | Comportamento de `--apply` |
|---|---|
| `pre-commit` framework | Adiciona/atualiza entry AXON em `.pre-commit-config.yaml` |
| `husky` | Gera wrapper text para o dev colar manualmente em `.husky/post-commit` |
| Nenhum | Sugere instalar `pre-commit` ou escreve direto em `.git/hooks/` com confirmação dupla |

### Hooks no escopo do AXON

| Hook | Evento | Consumidor |
|---|---|---|
| `post-commit` | captura `CodeChange` + `pb adr infer-commit` se sinalizado | dec-110, dec-111 |
| `pre-push` | snapshot final + sync de drafts | dec-111 |
| `post-merge` | revalida drafts via L1-full | dec-111 |
| `post-checkout` | revalida drafts via L1-full | dec-111 |

Todos falham silenciosamente. Nenhum bloqueia git.

### Migração de `pb adr hook-install`

- Vira alias deprecated por 1-2 releases
- Emite warning + redireciona para `pb hooks install`
- Próximo major remove

### Comportamento em CI

`pb hooks install` em ambiente não-interativo (sem TTY):

- Exit 1 com mensagem clara
- Nunca muta nada
- Doctor `--ci` mode (dec-114) reporta hook status sem corrigir

## Rationale

- **Hooks são contrato compartilhado** com toolchain do time. Mutação
  automática é classificada como inseguro em revisão de ferramenta.
- **`core.hooksPath` é destrutivo** — quebra husky silenciosamente,
  não é coexistência.
- **Pre-commit framework já gerencia chaining** — AXON entra como
  entry, framework cuida da orquestração.
- **`--apply` explícito** preserva confiança em ambientes
  corporativos / OSS-team.
- **Hooks falham silenciosamente** (dec-104) garante que problemas no
  AXON nunca bloqueiam dev.

## Consequences

- `src/axon/hooks/git_installer.py` refatorado:
  - Detector de toolchain
  - Geradores de integração por toolchain
  - Dry-run default; `--apply` obrigatório
- Novo módulo `axon.hooks.precommit_integration` para
  `.pre-commit-config.yaml`.
- Novo módulo `axon.hooks.husky_integration` (wrapper text).
- Nova CLI `pb hooks install` em `pb.py`.
- `pb adr hook-install` mantida como alias com `DeprecationWarning`
  por 2 releases.
- Hooks `post-merge` e `post-checkout` adicionados ao set padrão (para
  dec-111 L1-full triggers).
- Aceito como risco residual: dev sem nenhuma toolchain de hook
  precisa de comando explícito para captura derivada via hook —
  captura via MCP (dec-103) continua zero-fricção para agentes que
  falam MCP.
- Aceito como risco residual: husky users precisam colar wrapper
  manualmente — workaround documentado.
