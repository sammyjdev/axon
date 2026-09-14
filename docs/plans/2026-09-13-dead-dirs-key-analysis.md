# Diretórios mortos no índice: qual é a chave certa

Análise read-only de `postgresql://axon:axon@localhost:5434/axon` em 2026-09-13.
Nada foi escrito no banco e nada no disco foi tocado além de `test -d`.

## Escopo e método

- Universo: diretório de cada `file_path` (prefixo até a última `/`) em `embeddings`,
  `file_index` e `recall_embeddings`. 642 diretórios distintos ao todo.
- Morto = caminho absoluto que hoje não resolve com `test -d`.
- **As 216 pastas são exatamente os diretórios mortos que têm linhas em `embeddings`.**
  O total de mortos é maior (227 absolutos + 36 relativos); o recorte de 216 é o que
  bate com a sua contagem e é o que a tabela abaixo cobre. Os outros 47 estão na
  seção "Fora das 216".
- Chave proposta = nome de diretório canônico, porque é isso que `repo_identity()`
  usa (`~/.claude/axon/ROUTER.md`). `config/projects.json` entra só como tabela de
  alias `name -> path`, nunca como fonte da chave: os `name` de lá são legados
  (`Pharos`, `PitStopOS`, `Orion-AI`, `piloto_revvo`, `claude-skills`).

## Veredito em uma linha

| Classificação | Diretórios | Linhas em `embeddings` | Linhas em `file_index` |
|---|---:|---:|---:|
| RESOLVIDO | 213 | 6628 | 940 |
| AMBÍGUO | 1 | 31 | 8 |
| NÃO-REPO | 2 | 1119 | 356 |
| **total** | **216** | **7778** | **1304** |

213 dos 216 saem por estrutura de caminho, sem ambiguidade. Só três casos precisam
de decisão humana, e dois deles são a mesma decisão (vault não é repo).

## Rollup por chave proposta

| Chave proposta | Dirs | `embeddings` | `file_index` | No ROUTER? |
|---|---:|---:|---:|---|
| `aerus-game-master-platform` | 35 | 1525 | 185 | sim |
| `claude-usage-bar` | 2 | 9 | 2 | sim |
| `claude-usage-bar-rs` | 4 | 36 | 4 | sim |
| `glyph-kg` | 30 | 790 | 132 | sim |
| `gnomon-eval` | 21 | 385 | 62 | sim |
| `lina` | 4 | 20 | 6 | sim |
| `linkedin-content-manager` | 21 | 1574 | 172 | **não** |
| `lume` | 16 | 262 | 49 | sim |
| `orion-ai` | 9 | 545 | 74 | sim |
| `pharos` | 2 | 88 | 5 | sim |
| `pharos-backend` | 16 | 388 | 93 | sim |
| `pharos-frontend` | 6 | 60 | 15 | sim |
| `pitstop-os` | 11 | 384 | 38 | sim |
| `revvo` | 5 | 65 | 23 | **não** |
| `rpg-master-ai` | 31 | 497 | 80 | sim |
| `-` | 3 | 1150 | 364 | - |

`linkedin-content-manager` e `revvo` estão em `config/projects.json` mas **não** na
lista canônica do ROUTER. São indexados sem serem onboarded. Reescrever as linhas
deles cria chaves que o guard de drift não conhece: ou entram no ROUTER, ou a Fase 1
os trata como fora de escopo. Decisão sua, não do script.

## Regras usadas

| ID | Regra | Base |
|---|---|---|
| R1 | Repo migrou de `dev/<X>` para `dev/products/<X>` ou `dev/tools/<X>`; a chave é o nome do diretório, inalterado | `config/projects.json` + layout atual de `~/dev` |
| R2 | Alias de caixa/separador: `Pharos->pharos`, `PitStopOS->pitstop-os`, `Orion-AI->orion-ai`, `linkedin_content_manager->linkedin-content-manager`, `piloto_revvo->revvo` | `projects.json` mapeia `name` legado para o `path` atual |
| R3 | Repo aninhado declarado no ROUTER ganha chave própria, não a do pai | ROUTER.md: "A nested repo needs its own entry" |
| R4 | `~/vault/**` não pertence a repo nenhum | CLAUDE.md D1: dado e engine são separados |
| R5 | `pytest-of-*` sob tmpdir é lixo de teste | - |
| R6 | Caminho relativo não tem raiz: nenhuma regra textual resolve | - |
| R7 | Sufixo `-worktrees`: o worktree herda a chave do repo pai | convenção em `axon-worktrees`, `glyph-kg-worktrees`, `_worktrees/gnomon-eval-worktrees` |
| R8 | `_bench`, `_worktrees`, `_wt` são raízes de scratch, não repos | layout de `~/dev` |

Casamento de segmento é **exato**, nunca por prefixo. Prefixo faria
`claude-usage-bar` engolir `claude-usage-bar-rs` e `pharos` engolir `pharos-backend`.

## Os três casos que precisam de veredito

### `/Users/samdev/vault/AXON/Decisions` - 1113 linhas - NÃO-REPO

Não é repo e não deve ganhar chave de repo. É o vault, que por D1 vive fora da
engine. Duas coisas pioram o caso: os irmãos `vault/AXON/Architecture` (44) e
`vault/AXON/Sessions` (18) **existem** no disco, então só `Decisions` sumiu, e as
1113 linhas estão rachadas em dois ctx (`knowledge` 659, `personal` 454) para o
mesmo diretório. A regra que falta: um campo de origem que distinga `repo` de
`vault`, em vez de espremer as duas coisas na mesma coluna `project`. Sem isso,
qualquer chave que você inventar aqui é uma mentira que a Fase 2 vai herdar.

### `/Users/samdev/dev/gnomon-eval-src` - 31 linhas - AMBÍGUO

O segmento não está no ROUTER nem em `projects.json`. Por texto casa com
`gnomon-eval` só por prefixo, e prefixo é exatamente a regra que quebra
`claude-usage-bar` / `claude-usage-bar-rs`. A regra que falta: uma lista explícita
de alias histórico (`gnomon-eval-src -> gnomon-eval`) confirmada por quem sabe se
aquilo era o mesmo repo ou um checkout separado do upstream. 31 linhas não pagam
uma heurística de prefixo que estraga outros repos.

### `/Users/samdev/vault/work-notes` - 6 linhas - NÃO-REPO

Mesmo veredito de R4, com um agravante: o nome diz `work`, que é contexto restrito,
e as 6 linhas estão indexadas como `knowledge` (2) e `personal` (4) - ou seja, fora
da restrição. Pode ser só o nome da pasta, pode ser vazamento. A Fase 1 não deveria
re-chavear essas linhas; deveria **apagá-las** ou auditá-las antes de mais nada.

## Os dois casos que você citou não estão nas 216

Ambos vêm de `sessions.repo`, não de `embeddings.file_path`, e por isso ficam fora
do recorte. `sessions` tem 6 valores distintos de `repo` e uma linha cada:

| `sessions.repo` | Existe? | Chave proposta | Classificação | Regra |
|---|---|---|---|---|
| `/Users/samdev/dev/products/merit-worktrees/agent-issue-6` | não | `merit` | RESOLVIDO | R7 |
| `/Users/samdev/dev/_bench/maker-bench/gemini-3.7-flash-high__H3__r4` | sim | - | NÃO-REPO | R8 |
| `/Users/samdev/dev/lina` | não | `lina` | RESOLVIDO | R1 |
| `/Users/samdev/dev/rpg-master-ai` | não | `rpg-master-ai` | RESOLVIDO | R1 |
| `/Users/samdev/dev/axon` | sim | `axon` | RESOLVIDO | R1 |
| `axon` | n/a | `axon` | RESOLVIDO | já é chave, não caminho |

**merit-worktrees/agent-issue-6**: resolvido, não ambíguo. `<repo>-worktrees/<branch>`
é convenção confirmada em três lugares vivos (`axon-worktrees/agent-issue-141`,
`products/glyph-kg-worktrees`, `_worktrees/gnomon-eval-worktrees`), e `merit` está no
ROUTER com `products/merit` vivo no disco. O worktree é um checkout do mesmo repo:
a chave é `merit`, o branch não entra na chave.

O que **não** é resolvível por R7: os pools genéricos `dev/_wt/axon-summary-cap` e
`dev/_wt/scope-creep`, onde o filho é o nome do branch e nada no caminho nomeia o
repo. Hoje não há linhas nesses caminhos; se aparecerem, a regra que falta é gravar
a chave na captura, em vez de tentar deduzi-la do caminho depois.

**_bench/maker-bench/<arm>**: NÃO-REPO. É clone descartável de arm de benchmark.
Textualmente ele é indistinguível de um repo de verdade - tem `.git`, tem `src/` -
e é por isso que a regra que falta é uma **denylist explícita de raízes de scratch**
(`_bench`, `_worktrees`, `_wt`, tmpdir), não uma heurística. O `__H3__r4` no nome é
acidente do harness, não algo em que se possa confiar.

## Fora das 216

- **36 diretórios relativos**, todos `src/axon/**`, só em `recall_embeddings` (~1100
  linhas). Não são "mortos", são não-resolvíveis: foram gravados sem raiz. Nenhuma
  regra textual recupera o repo, embora `src/axon` só possa ser `axon` na prática.
  Classificação honesta: AMBÍGUO por R6. É um segundo bug de chaveamento,
  independente do primeiro, e a Fase 1 deveria cobrir os dois ou dizer que não cobre.
- **2 diretórios de `pytest-of-samdev`**, só em `file_index`, 1 linha cada. NÃO-REPO
  por R5. São resíduo de teste que escreveu no índice de produção - mesma classe do
  que o commit 839ed6d consertou.
- **9 diretórios mortos** com linhas só em `file_index` e zero em `embeddings`.

## A coluna `project` não é recuperável

`embeddings.project` tem **233 valores distintos** para ~20 repos reais. Ela está
guardando o nome do diretório-pai do arquivo, não o repo: os 216 dirs mortos
carregam valores como `tests`, `docs`, `adr`, `src`, `utils`, `hooks`. Amostra:

| Chave proposta | Valores distintos de `project` hoje | Amostra |
|---|---:|---|
| `aerus-game-master-platform` | 33 | `aerus-game-master-platform`, `agents`, `ai-ops`, `api`, `application` |
| `claude-usage-bar` | 2 | `claude-usage-bar`, `specs` |
| `claude-usage-bar-rs` | 4 | `claude-usage-bar-rs`, `design`, `specs`, `superpowers` |
| `glyph-kg` | 19 | `agents`, `architecture`, `baseline`, `code`, `decisions` |
| `gnomon-eval` | 20 | `2026-07-02-ab-recall`, `adr`, `articles`, `config`, `dataset` |
| `lina` | 4 | `lina`, `lina_core`, `shared-schemas`, `tests` |
| `linkedin-content-manager` | 21 | `adr`, `agents`, `analytics`, `architecture`, `audits` |
| `lume` | 14 | `.github`, `__tests__`, `adr`, `concepts`, `demo` |
| `orion-ai` | 9 | `Orion-AI`, `actions`, `adr`, `app`, `docs` |
| `pharos` | 2 | `docs`, `specs` |
| `pharos-backend` | 16 | `alembic`, `api`, `app`, `core`, `docs` |
| `pharos-frontend` | 6 | `design`, `e2e`, `hooks`, `lib`, `pharos-frontend` |
| `pitstop-os` | 11 | `PitStopOS`, `alembic`, `docs`, `e2e`, `pitstopos` |
| `revvo` | 5 | `api`, `lib`, `piloto_revvo`, `specs`, `src` |
| `rpg-master-ai` | 27 | `.github`, `adr`, `agents`, `app`, `application` |

Consequência para a Fase 1: `project` não serve nem como desempate nem como
verificação. A chave tem que sair do caminho e sobrescrever `project` inteiro, não
só nas 216 pastas mortas - os diretórios **vivos** têm o mesmo defeito.

## Tabela completa - 216 diretórios

| # | Diretório | `embeddings` | `file_index` | Chave proposta | Classificação | Regra |
|---:|---|---:|---:|---|---|---|
| 1 | `~/dev/aerus-game-master-platform` | 22 | 2 | `aerus-game-master-platform` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 2 | `~/dev/aerus-game-master-platform/backend` | 6 | 4 | `aerus-game-master-platform` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 3 | `~/dev/aerus-game-master-platform/backend/config` | 34 | 10 | `aerus-game-master-platform` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 4 | `~/dev/aerus-game-master-platform/backend/e2e` | 29 | 3 | `aerus-game-master-platform` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 5 | `~/dev/aerus-game-master-platform/backend/eval` | 135 | 6 | `aerus-game-master-platform` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 6 | `~/dev/aerus-game-master-platform/backend/eval/topics` | 13 | 8 | `aerus-game-master-platform` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 7 | `~/dev/aerus-game-master-platform/backend/scripts` | 6 | 1 | `aerus-game-master-platform` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 8 | `~/dev/aerus-game-master-platform/backend/src` | 382 | 26 | `aerus-game-master-platform` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 9 | `~/dev/aerus-game-master-platform/backend/src/application` | 1 | 1 | `aerus-game-master-platform` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 10 | `~/dev/aerus-game-master-platform/backend/src/application/billing` | 2 | 2 | `aerus-game-master-platform` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 11 | `~/dev/aerus-game-master-platform/backend/src/infrastructure` | 1 | 1 | `aerus-game-master-platform` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 12 | `~/dev/aerus-game-master-platform/backend/src/infrastructure/config` | 9 | 2 | `aerus-game-master-platform` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 13 | `~/dev/aerus-game-master-platform/backend/tests` | 387 | 27 | `aerus-game-master-platform` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 14 | `~/dev/aerus-game-master-platform/docs` | 347 | 37 | `aerus-game-master-platform` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 15 | `~/dev/aerus-game-master-platform/docs/ai-ops` | 5 | 2 | `aerus-game-master-platform` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 16 | `~/dev/aerus-game-master-platform/docs/ai-ops/agents` | 7 | 7 | `aerus-game-master-platform` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 17 | `~/dev/aerus-game-master-platform/docs/ai-ops/evidence` | 2 | 1 | `aerus-game-master-platform` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 18 | `~/dev/aerus-game-master-platform/docs/ai-ops/harness` | 4 | 3 | `aerus-game-master-platform` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 19 | `~/dev/aerus-game-master-platform/docs/ai-ops/migration` | 6 | 4 | `aerus-game-master-platform` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 20 | `~/dev/aerus-game-master-platform/docs/ai-ops/rules` | 2 | 2 | `aerus-game-master-platform` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 21 | `~/dev/aerus-game-master-platform/docs/ai-ops/skills` | 3 | 2 | `aerus-game-master-platform` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 22 | `~/dev/aerus-game-master-platform/docs/ai-ops/specs` | 5 | 5 | `aerus-game-master-platform` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 23 | `~/dev/aerus-game-master-platform/frontend` | 19 | 3 | `aerus-game-master-platform` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 24 | `~/dev/aerus-game-master-platform/frontend/public/audio` | 1 | 1 | `aerus-game-master-platform` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 25 | `~/dev/aerus-game-master-platform/frontend/src/api` | 22 | 1 | `aerus-game-master-platform` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 26 | `~/dev/aerus-game-master-platform/frontend/src/constants` | 1 | 1 | `aerus-game-master-platform` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 27 | `~/dev/aerus-game-master-platform/frontend/src/debug` | 8 | 1 | `aerus-game-master-platform` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 28 | `~/dev/aerus-game-master-platform/frontend/src/features/game` | 1 | 1 | `aerus-game-master-platform` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 29 | `~/dev/aerus-game-master-platform/frontend/src/hooks` | 21 | 5 | `aerus-game-master-platform` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 30 | `~/dev/aerus-game-master-platform/frontend/src/i18n` | 1 | 1 | `aerus-game-master-platform` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 31 | `~/dev/aerus-game-master-platform/frontend/src/store` | 6 | 2 | `aerus-game-master-platform` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 32 | `~/dev/aerus-game-master-platform/frontend/src/test` | 1 | 1 | `aerus-game-master-platform` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 33 | `~/dev/aerus-game-master-platform/frontend/src/types` | 4 | 3 | `aerus-game-master-platform` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 34 | `~/dev/aerus-game-master-platform/lore` | 25 | 8 | `aerus-game-master-platform` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 35 | `~/dev/aerus-game-master-platform/scripts` | 7 | 1 | `aerus-game-master-platform` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 36 | `~/dev/claude-usage-bar` | 2 | 1 | `claude-usage-bar` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 37 | `~/dev/claude-usage-bar/docs/superpowers/specs` | 7 | 1 | `claude-usage-bar` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 38 | `~/dev/claude-usage-bar-rs` | 14 | 1 | `claude-usage-bar-rs` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 39 | `~/dev/claude-usage-bar-rs/docs/design` | 7 | 1 | `claude-usage-bar-rs` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 40 | `~/dev/claude-usage-bar-rs/docs/superpowers` | 3 | 1 | `claude-usage-bar-rs` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 41 | `~/dev/claude-usage-bar-rs/docs/superpowers/specs` | 12 | 1 | `claude-usage-bar-rs` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 42 | `~/dev/glyph-kg` | 21 | 7 | `glyph-kg` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 43 | `~/dev/glyph-kg/docs` | 37 | 6 | `glyph-kg` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 44 | `~/dev/glyph-kg/docs/agents` | 4 | 0 | `glyph-kg` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 45 | `~/dev/glyph-kg/docs/decisions` | 40 | 10 | `glyph-kg` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 46 | `~/dev/glyph-kg/docs/superpowers/specs` | 15 | 2 | `glyph-kg` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 47 | `~/dev/glyph-kg/eval` | 2 | 1 | `glyph-kg` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 48 | `~/dev/glyph-kg/glyph` | 3 | 2 | `glyph-kg` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 49 | `~/dev/glyph-kg/glyph/baseline` | 4 | 2 | `glyph-kg` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 50 | `~/dev/glyph-kg/glyph/embed` | 9 | 4 | `glyph-kg` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 51 | `~/dev/glyph-kg/glyph/eval` | 29 | 10 | `glyph-kg` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 52 | `~/dev/glyph-kg/glyph/extract` | 5 | 2 | `glyph-kg` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 53 | `~/dev/glyph-kg/glyph/extract/code` | 24 | 3 | `glyph-kg` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 54 | `~/dev/glyph-kg/glyph/extract/document` | 36 | 10 | `glyph-kg` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 55 | `~/dev/glyph-kg/glyph/integration` | 4 | 2 | `glyph-kg` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 56 | `~/dev/glyph-kg/glyph/model` | 6 | 5 | `glyph-kg` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 57 | `~/dev/glyph-kg/glyph/retrieval` | 27 | 5 | `glyph-kg` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 58 | `~/dev/glyph-kg/glyph/store` | 33 | 4 | `glyph-kg` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 59 | `~/dev/glyph-kg/scripts` | 48 | 8 | `glyph-kg` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 60 | `~/dev/glyph-kg/tests` | 14 | 2 | `glyph-kg` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 61 | `~/dev/glyph-kg/tests/architecture` | 8 | 1 | `glyph-kg` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 62 | `~/dev/glyph-kg/tests/baseline` | 7 | 2 | `glyph-kg` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 63 | `~/dev/glyph-kg/tests/embed` | 8 | 3 | `glyph-kg` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 64 | `~/dev/glyph-kg/tests/eval` | 68 | 11 | `glyph-kg` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 65 | `~/dev/glyph-kg/tests/extract` | 5 | 1 | `glyph-kg` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 66 | `~/dev/glyph-kg/tests/extract/code` | 20 | 2 | `glyph-kg` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 67 | `~/dev/glyph-kg/tests/extract/document` | 160 | 13 | `glyph-kg` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 68 | `~/dev/glyph-kg/tests/integration` | 9 | 2 | `glyph-kg` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 69 | `~/dev/glyph-kg/tests/model` | 22 | 4 | `glyph-kg` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 70 | `~/dev/glyph-kg/tests/retrieval` | 68 | 4 | `glyph-kg` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 71 | `~/dev/glyph-kg/tests/store` | 54 | 4 | `glyph-kg` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 72 | `~/dev/gnomon-eval` | 5 | 1 | `gnomon-eval` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 73 | `~/dev/gnomon-eval/datasets/second_brain_example` | 4 | 0 | `gnomon-eval` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 74 | `~/dev/gnomon-eval/docs` | 40 | 5 | `gnomon-eval` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 75 | `~/dev/gnomon-eval/docs/adr` | 37 | 8 | `gnomon-eval` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 76 | `~/dev/gnomon-eval/docs/articles` | 14 | 1 | `gnomon-eval` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 77 | `~/dev/gnomon-eval/docs/superpowers/specs` | 9 | 1 | `gnomon-eval` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 78 | `~/dev/gnomon-eval/results/2026-07-02-ab-recall` | 2 | 0 | `gnomon-eval` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 79 | `~/dev/gnomon-eval/src/gnomon` | 9 | 3 | `gnomon-eval` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 80 | `~/dev/gnomon-eval/src/gnomon/config` | 8 | 3 | `gnomon-eval` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 81 | `~/dev/gnomon-eval/src/gnomon/dataset` | 3 | 2 | `gnomon-eval` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 82 | `~/dev/gnomon-eval/src/gnomon/domain` | 9 | 3 | `gnomon-eval` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 83 | `~/dev/gnomon-eval/src/gnomon/gate` | 2 | 2 | `gnomon-eval` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 84 | `~/dev/gnomon-eval/src/gnomon/judge` | 17 | 5 | `gnomon-eval` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 85 | `~/dev/gnomon-eval/src/gnomon/metrics` | 5 | 3 | `gnomon-eval` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 86 | `~/dev/gnomon-eval/src/gnomon/reporting` | 12 | 2 | `gnomon-eval` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 87 | `~/dev/gnomon-eval/src/gnomon/runner` | 5 | 2 | `gnomon-eval` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 88 | `~/dev/gnomon-eval/src/gnomon/targets` | 13 | 3 | `gnomon-eval` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 89 | `~/dev/gnomon-eval/tests/gate` | 1 | 1 | `gnomon-eval` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 90 | `~/dev/gnomon-eval/tests/integration` | 9 | 2 | `gnomon-eval` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 91 | `~/dev/gnomon-eval/tests/reproducibility` | 4 | 1 | `gnomon-eval` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 92 | `~/dev/gnomon-eval/tests/unit` | 177 | 14 | `gnomon-eval` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 93 | `~/dev/lina` | 12 | 2 | `lina` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 94 | `~/dev/lina/core/src/lina_core` | 3 | 2 | `lina` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 95 | `~/dev/lina/core/tests` | 4 | 1 | `lina` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 96 | `~/dev/lina/shared-schemas` | 1 | 1 | `lina` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 97 | `~/dev/linkedin_content_manager` | 62 | 5 | `linkedin-content-manager` | RESOLVIDO | R2 alias de projects.json (caixa/separador): linkedin_content_manager -> linkedin-content-manager |
| 98 | `~/dev/linkedin_content_manager/agents` | 169 | 8 | `linkedin-content-manager` | RESOLVIDO | R2 alias de projects.json (caixa/separador): linkedin_content_manager -> linkedin-content-manager |
| 99 | `~/dev/linkedin_content_manager/analytics` | 47 | 5 | `linkedin-content-manager` | RESOLVIDO | R2 alias de projects.json (caixa/separador): linkedin_content_manager -> linkedin-content-manager |
| 100 | `~/dev/linkedin_content_manager/claude_project` | 42 | 6 | `linkedin-content-manager` | RESOLVIDO | R2 alias de projects.json (caixa/separador): linkedin_content_manager -> linkedin-content-manager |
| 101 | `~/dev/linkedin_content_manager/comments` | 19 | 4 | `linkedin-content-manager` | RESOLVIDO | R2 alias de projects.json (caixa/separador): linkedin_content_manager -> linkedin-content-manager |
| 102 | `~/dev/linkedin_content_manager/config` | 3 | 2 | `linkedin-content-manager` | RESOLVIDO | R2 alias de projects.json (caixa/separador): linkedin_content_manager -> linkedin-content-manager |
| 103 | `~/dev/linkedin_content_manager/config/prompts` | 74 | 10 | `linkedin-content-manager` | RESOLVIDO | R2 alias de projects.json (caixa/separador): linkedin_content_manager -> linkedin-content-manager |
| 104 | `~/dev/linkedin_content_manager/config/prompts/writer` | 36 | 11 | `linkedin-content-manager` | RESOLVIDO | R2 alias de projects.json (caixa/separador): linkedin_content_manager -> linkedin-content-manager |
| 105 | `~/dev/linkedin_content_manager/docs` | 48 | 2 | `linkedin-content-manager` | RESOLVIDO | R2 alias de projects.json (caixa/separador): linkedin_content_manager -> linkedin-content-manager |
| 106 | `~/dev/linkedin_content_manager/docs/adr` | 3 | 1 | `linkedin-content-manager` | RESOLVIDO | R2 alias de projects.json (caixa/separador): linkedin_content_manager -> linkedin-content-manager |
| 107 | `~/dev/linkedin_content_manager/docs/architecture` | 30 | 6 | `linkedin-content-manager` | RESOLVIDO | R2 alias de projects.json (caixa/separador): linkedin_content_manager -> linkedin-content-manager |
| 108 | `~/dev/linkedin_content_manager/docs/audits` | 13 | 4 | `linkedin-content-manager` | RESOLVIDO | R2 alias de projects.json (caixa/separador): linkedin_content_manager -> linkedin-content-manager |
| 109 | `~/dev/linkedin_content_manager/docs/editorial` | 27 | 12 | `linkedin-content-manager` | RESOLVIDO | R2 alias de projects.json (caixa/separador): linkedin_content_manager -> linkedin-content-manager |
| 110 | `~/dev/linkedin_content_manager/docs/operations` | 2 | 1 | `linkedin-content-manager` | RESOLVIDO | R2 alias de projects.json (caixa/separador): linkedin_content_manager -> linkedin-content-manager |
| 111 | `~/dev/linkedin_content_manager/lambda_handlers` | 66 | 6 | `linkedin-content-manager` | RESOLVIDO | R2 alias de projects.json (caixa/separador): linkedin_content_manager -> linkedin-content-manager |
| 112 | `~/dev/linkedin_content_manager/models` | 11 | 2 | `linkedin-content-manager` | RESOLVIDO | R2 alias de projects.json (caixa/separador): linkedin_content_manager -> linkedin-content-manager |
| 113 | `~/dev/linkedin_content_manager/pipeline` | 98 | 14 | `linkedin-content-manager` | RESOLVIDO | R2 alias de projects.json (caixa/separador): linkedin_content_manager -> linkedin-content-manager |
| 114 | `~/dev/linkedin_content_manager/scripts` | 30 | 4 | `linkedin-content-manager` | RESOLVIDO | R2 alias de projects.json (caixa/separador): linkedin_content_manager -> linkedin-content-manager |
| 115 | `~/dev/linkedin_content_manager/tests` | 627 | 44 | `linkedin-content-manager` | RESOLVIDO | R2 alias de projects.json (caixa/separador): linkedin_content_manager -> linkedin-content-manager |
| 116 | `~/dev/linkedin_content_manager/tools` | 29 | 5 | `linkedin-content-manager` | RESOLVIDO | R2 alias de projects.json (caixa/separador): linkedin_content_manager -> linkedin-content-manager |
| 117 | `~/dev/linkedin_content_manager/utils` | 138 | 20 | `linkedin-content-manager` | RESOLVIDO | R2 alias de projects.json (caixa/separador): linkedin_content_manager -> linkedin-content-manager |
| 118 | `~/dev/lume` | 15 | 4 | `lume` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 119 | `~/dev/lume/.github` | 8 | 1 | `lume` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 120 | `~/dev/lume/assets/concepts` | 1 | 1 | `lume` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 121 | `~/dev/lume/docs` | 120 | 14 | `lume` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 122 | `~/dev/lume/docs/adr` | 24 | 5 | `lume` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 123 | `~/dev/lume/docs/runbooks` | 10 | 3 | `lume` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 124 | `~/dev/lume/docs/superpowers/specs` | 31 | 3 | `lume` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 125 | `~/dev/lume/frontend` | 10 | 4 | `lume` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 126 | `~/dev/lume/frontend/src/__tests__` | 1 | 1 | `lume` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 127 | `~/dev/lume/frontend/src/__tests__/lib` | 1 | 1 | `lume` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 128 | `~/dev/lume/frontend/src/hooks` | 3 | 1 | `lume` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 129 | `~/dev/lume/frontend/src/lib` | 13 | 3 | `lume` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 130 | `~/dev/lume/frontend/src/lib/demo` | 20 | 5 | `lume` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 131 | `~/dev/lume/frontend/src/types` | 1 | 1 | `lume` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 132 | `~/dev/lume/landing` | 3 | 1 | `lume` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 133 | `~/dev/lume/specs` | 1 | 1 | `lume` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 134 | `~/dev/Orion-AI` | 16 | 4 | `orion-ai` | RESOLVIDO | R2 alias de projects.json (caixa/separador): Orion-AI -> orion-ai |
| 135 | `~/dev/Orion-AI/app` | 58 | 10 | `orion-ai` | RESOLVIDO | R2 alias de projects.json (caixa/separador): Orion-AI -> orion-ai |
| 136 | `~/dev/Orion-AI/app/actions` | 12 | 12 | `orion-ai` | RESOLVIDO | R2 alias de projects.json (caixa/separador): Orion-AI -> orion-ai |
| 137 | `~/dev/Orion-AI/app/mcp` | 16 | 2 | `orion-ai` | RESOLVIDO | R2 alias de projects.json (caixa/separador): Orion-AI -> orion-ai |
| 138 | `~/dev/Orion-AI/docs` | 9 | 2 | `orion-ai` | RESOLVIDO | R2 alias de projects.json (caixa/separador): Orion-AI -> orion-ai |
| 139 | `~/dev/Orion-AI/docs/adr` | 37 | 15 | `orion-ai` | RESOLVIDO | R2 alias de projects.json (caixa/separador): Orion-AI -> orion-ai |
| 140 | `~/dev/Orion-AI/docs/superpowers/specs` | 111 | 12 | `orion-ai` | RESOLVIDO | R2 alias de projects.json (caixa/separador): Orion-AI -> orion-ai |
| 141 | `~/dev/Orion-AI/prompts` | 5 | 1 | `orion-ai` | RESOLVIDO | R2 alias de projects.json (caixa/separador): Orion-AI -> orion-ai |
| 142 | `~/dev/Orion-AI/tests` | 281 | 16 | `orion-ai` | RESOLVIDO | R2 alias de projects.json (caixa/separador): Orion-AI -> orion-ai |
| 143 | `~/dev/Pharos/docs` | 71 | 3 | `pharos` | RESOLVIDO | R2 alias de projects.json (caixa/separador): Pharos -> pharos |
| 144 | `~/dev/Pharos/docs/superpowers/specs` | 17 | 2 | `pharos` | RESOLVIDO | R2 alias de projects.json (caixa/separador): Pharos -> pharos |
| 145 | `~/dev/Pharos/pharos-backend` | 14 | 4 | `pharos-backend` | RESOLVIDO | R3 repo aninhado declarado no ROUTER: ganha chave propria |
| 146 | `~/dev/Pharos/pharos-backend/alembic` | 3 | 1 | `pharos-backend` | RESOLVIDO | R3 repo aninhado declarado no ROUTER: ganha chave propria |
| 147 | `~/dev/Pharos/pharos-backend/alembic/versions` | 17 | 7 | `pharos-backend` | RESOLVIDO | R3 repo aninhado declarado no ROUTER: ganha chave propria |
| 148 | `~/dev/Pharos/pharos-backend/app` | 2 | 2 | `pharos-backend` | RESOLVIDO | R3 repo aninhado declarado no ROUTER: ganha chave propria |
| 149 | `~/dev/Pharos/pharos-backend/app/api` | 1 | 1 | `pharos-backend` | RESOLVIDO | R3 repo aninhado declarado no ROUTER: ganha chave propria |
| 150 | `~/dev/Pharos/pharos-backend/app/api/v1` | 33 | 8 | `pharos-backend` | RESOLVIDO | R3 repo aninhado declarado no ROUTER: ganha chave propria |
| 151 | `~/dev/Pharos/pharos-backend/app/core` | 12 | 5 | `pharos-backend` | RESOLVIDO | R3 repo aninhado declarado no ROUTER: ganha chave propria |
| 152 | `~/dev/Pharos/pharos-backend/app/models` | 16 | 16 | `pharos-backend` | RESOLVIDO | R3 repo aninhado declarado no ROUTER: ganha chave propria |
| 153 | `~/dev/Pharos/pharos-backend/app/schemas` | 6 | 6 | `pharos-backend` | RESOLVIDO | R3 repo aninhado declarado no ROUTER: ganha chave propria |
| 154 | `~/dev/Pharos/pharos-backend/app/services` | 62 | 9 | `pharos-backend` | RESOLVIDO | R3 repo aninhado declarado no ROUTER: ganha chave propria |
| 155 | `~/dev/Pharos/pharos-backend/app/workers` | 5 | 2 | `pharos-backend` | RESOLVIDO | R3 repo aninhado declarado no ROUTER: ganha chave propria |
| 156 | `~/dev/Pharos/pharos-backend/data/demo/soja-br-nl-2026` | 6 | 5 | `pharos-backend` | RESOLVIDO | R3 repo aninhado declarado no ROUTER: ganha chave propria |
| 157 | `~/dev/Pharos/pharos-backend/data/rules` | 15 | 4 | `pharos-backend` | RESOLVIDO | R3 repo aninhado declarado no ROUTER: ganha chave propria |
| 158 | `~/dev/Pharos/pharos-backend/docs` | 61 | 3 | `pharos-backend` | RESOLVIDO | R3 repo aninhado declarado no ROUTER: ganha chave propria |
| 159 | `~/dev/Pharos/pharos-backend/scripts` | 3 | 2 | `pharos-backend` | RESOLVIDO | R3 repo aninhado declarado no ROUTER: ganha chave propria |
| 160 | `~/dev/Pharos/pharos-backend/tests` | 132 | 18 | `pharos-backend` | RESOLVIDO | R3 repo aninhado declarado no ROUTER: ganha chave propria |
| 161 | `~/dev/Pharos/pharos-frontend` | 21 | 5 | `pharos-frontend` | RESOLVIDO | R3 repo aninhado declarado no ROUTER: ganha chave propria |
| 162 | `~/dev/Pharos/pharos-frontend/components/ui` | 8 | 1 | `pharos-frontend` | RESOLVIDO | R3 repo aninhado declarado no ROUTER: ganha chave propria |
| 163 | `~/dev/Pharos/pharos-frontend/design` | 5 | 1 | `pharos-frontend` | RESOLVIDO | R3 repo aninhado declarado no ROUTER: ganha chave propria |
| 164 | `~/dev/Pharos/pharos-frontend/e2e` | 1 | 1 | `pharos-frontend` | RESOLVIDO | R3 repo aninhado declarado no ROUTER: ganha chave propria |
| 165 | `~/dev/Pharos/pharos-frontend/hooks` | 10 | 2 | `pharos-frontend` | RESOLVIDO | R3 repo aninhado declarado no ROUTER: ganha chave propria |
| 166 | `~/dev/Pharos/pharos-frontend/lib` | 15 | 5 | `pharos-frontend` | RESOLVIDO | R3 repo aninhado declarado no ROUTER: ganha chave propria |
| 167 | `~/dev/PitStopOS` | 55 | 5 | `pitstop-os` | RESOLVIDO | R2 alias de projects.json (caixa/separador): PitStopOS -> pitstop-os |
| 168 | `~/dev/PitStopOS/alembic` | 3 | 1 | `pitstop-os` | RESOLVIDO | R2 alias de projects.json (caixa/separador): PitStopOS -> pitstop-os |
| 169 | `~/dev/PitStopOS/alembic/versions` | 6 | 3 | `pitstop-os` | RESOLVIDO | R2 alias de projects.json (caixa/separador): PitStopOS -> pitstop-os |
| 170 | `~/dev/PitStopOS/docs` | 7 | 2 | `pitstop-os` | RESOLVIDO | R2 alias de projects.json (caixa/separador): PitStopOS -> pitstop-os |
| 171 | `~/dev/PitStopOS/docs/superpowers/specs` | 7 | 1 | `pitstop-os` | RESOLVIDO | R2 alias de projects.json (caixa/separador): PitStopOS -> pitstop-os |
| 172 | `~/dev/PitStopOS/scripts` | 14 | 3 | `pitstop-os` | RESOLVIDO | R2 alias de projects.json (caixa/separador): PitStopOS -> pitstop-os |
| 173 | `~/dev/PitStopOS/src/pitstopos` | 221 | 12 | `pitstop-os` | RESOLVIDO | R2 alias de projects.json (caixa/separador): PitStopOS -> pitstop-os |
| 174 | `~/dev/PitStopOS/tests` | 48 | 6 | `pitstop-os` | RESOLVIDO | R2 alias de projects.json (caixa/separador): PitStopOS -> pitstop-os |
| 175 | `~/dev/PitStopOS/tests/e2e` | 2 | 1 | `pitstop-os` | RESOLVIDO | R2 alias de projects.json (caixa/separador): PitStopOS -> pitstop-os |
| 176 | `~/dev/PitStopOS/tests/unit` | 19 | 3 | `pitstop-os` | RESOLVIDO | R2 alias de projects.json (caixa/separador): PitStopOS -> pitstop-os |
| 177 | `~/dev/PitStopOS/tests/usability` | 2 | 1 | `pitstop-os` | RESOLVIDO | R2 alias de projects.json (caixa/separador): PitStopOS -> pitstop-os |
| 178 | `~/dev/piloto_revvo` | 10 | 5 | `revvo` | RESOLVIDO | R2 alias de projects.json (caixa/separador): piloto_revvo -> revvo |
| 179 | `~/dev/piloto_revvo/docs/superpowers/specs` | 14 | 1 | `revvo` | RESOLVIDO | R2 alias de projects.json (caixa/separador): piloto_revvo -> revvo |
| 180 | `~/dev/piloto_revvo/src` | 7 | 3 | `revvo` | RESOLVIDO | R2 alias de projects.json (caixa/separador): piloto_revvo -> revvo |
| 181 | `~/dev/piloto_revvo/src/lib` | 29 | 9 | `revvo` | RESOLVIDO | R2 alias de projects.json (caixa/separador): piloto_revvo -> revvo |
| 182 | `~/dev/piloto_revvo/src/routes/api` | 5 | 5 | `revvo` | RESOLVIDO | R2 alias de projects.json (caixa/separador): piloto_revvo -> revvo |
| 183 | `~/dev/rpg-master-ai` | 46 | 2 | `rpg-master-ai` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 184 | `~/dev/rpg-master-ai/.github` | 10 | 1 | `rpg-master-ai` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 185 | `~/dev/rpg-master-ai/.github/instructions` | 24 | 6 | `rpg-master-ai` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 186 | `~/dev/rpg-master-ai/.github/prompts` | 10 | 3 | `rpg-master-ai` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 187 | `~/dev/rpg-master-ai/app/src/integrationTest/java/com/rpgmaster/app/benchmark` | 18 | 0 | `rpg-master-ai` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 188 | `~/dev/rpg-master-ai/app/src/integrationTest/java/com/rpgmaster/app/eval` | 2 | 0 | `rpg-master-ai` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 189 | `~/dev/rpg-master-ai/app/src/integrationTest/java/com/rpgmaster/app/integration` | 18 | 2 | `rpg-master-ai` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 190 | `~/dev/rpg-master-ai/app/src/main/java/com/rpgmaster/app` | 1 | 1 | `rpg-master-ai` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 191 | `~/dev/rpg-master-ai/app/src/main/java/com/rpgmaster/app/adapter/inbound` | 5 | 2 | `rpg-master-ai` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 192 | `~/dev/rpg-master-ai/app/src/main/java/com/rpgmaster/app/adapter/inbound/rest` | 30 | 3 | `rpg-master-ai` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 193 | `~/dev/rpg-master-ai/app/src/main/java/com/rpgmaster/app/adapter/outbound` | 35 | 7 | `rpg-master-ai` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 194 | `~/dev/rpg-master-ai/app/src/main/java/com/rpgmaster/app/adapter/outbound/persistence` | 14 | 2 | `rpg-master-ai` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 195 | `~/dev/rpg-master-ai/app/src/main/java/com/rpgmaster/app/application` | 9 | 2 | `rpg-master-ai` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 196 | `~/dev/rpg-master-ai/app/src/main/java/com/rpgmaster/app/application/port` | 16 | 6 | `rpg-master-ai` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 197 | `~/dev/rpg-master-ai/app/src/main/java/com/rpgmaster/app/config` | 16 | 7 | `rpg-master-ai` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 198 | `~/dev/rpg-master-ai/app/src/main/java/com/rpgmaster/app/observability` | 10 | 3 | `rpg-master-ai` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 199 | `~/dev/rpg-master-ai/app/src/test/java/com/rpgmaster/app/adapter/outbound` | 6 | 0 | `rpg-master-ai` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 200 | `~/dev/rpg-master-ai/app/src/test/java/com/rpgmaster/app/application` | 5 | 0 | `rpg-master-ai` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 201 | `~/dev/rpg-master-ai/app/src/test/java/com/rpgmaster/app/architecture` | 1 | 1 | `rpg-master-ai` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 202 | `~/dev/rpg-master-ai/app/src/test/java/com/rpgmaster/app/config` | 2 | 0 | `rpg-master-ai` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 203 | `~/dev/rpg-master-ai/app/src/test/java/com/rpgmaster/app/eval` | 20 | 0 | `rpg-master-ai` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 204 | `~/dev/rpg-master-ai/app/src/test/java/com/rpgmaster/app/unit` | 38 | 6 | `rpg-master-ai` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 205 | `~/dev/rpg-master-ai/docs` | 71 | 6 | `rpg-master-ai` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 206 | `~/dev/rpg-master-ai/docs/adr` | 25 | 13 | `rpg-master-ai` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 207 | `~/dev/rpg-master-ai/docs/agents` | 4 | 0 | `rpg-master-ai` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 208 | `~/dev/rpg-master-ai/docs/superpowers` | 15 | 0 | `rpg-master-ai` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 209 | `~/dev/rpg-master-ai/docs/superpowers/specs` | 28 | 0 | `rpg-master-ai` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 210 | `~/dev/rpg-master-ai/eval/gnomon` | 2 | 0 | `rpg-master-ai` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 211 | `~/dev/rpg-master-ai/eval/reports` | 6 | 0 | `rpg-master-ai` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 212 | `~/dev/rpg-master-ai/infra/scripts` | 1 | 0 | `rpg-master-ai` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 213 | `~/dev/rpg-master-ai/shared-domain/src/main/java/com/rpgmaster/domain` | 9 | 7 | `rpg-master-ai` | RESOLVIDO | R1 repo movido para `dev/products` ou `dev/tools`; chave = nome do diretorio canonico |
| 214 | `~/dev/gnomon-eval-src` | 31 | 8 | - | AMBÍGUO | R0 segmento nao esta no ROUTER nem em projects.json |
| 215 | `~/vault/AXON/Decisions` | 1113 | 355 | - | NÃO-REPO | R4 vault fica fora do engine (D1): nenhum repo o possui |
| 216 | `~/vault/work-notes` | 6 | 1 | - | NÃO-REPO | R4 vault fica fora do engine (D1): nenhum repo o possui |

