# Design: Acoplar o AXON no fluxo global do Claude Code (Spec A)

Data: 2026-06-19
Status: aprovado (aguardando review do spec escrito)
Escopo: **A** (acoplamento). A resiliência de provider (cadeia gpt-oss multi-provider) é o **Spec B**, separado.

## Contexto

O AXON existe e está publicado (master, com a integração rtkx+GLYPH). O objetivo é
acoplá-lo ao fluxo padrão do Claude Code (CLAUDE.md global) para que ele comece a
**salvar documentação, estados de projeto entre execuções e código** - virando um
"second brain" cross-sessão e cross-agente.

Restrições e fatos que moldam o design (verificados no código):
- `axon serve` (MCP, stdio) sobe em modo mínimo só com **SQLite**; Redis/Qdrant/Ollama
  são lazy e opcionais (falham só se a ferramenta que depende deles for chamada).
- Captura/recall/busca de código funcionam **sem API key**; só `ask` (roteamento) e o
  judge de ADR (no push) tocam a nuvem.
- `~/.claude` é um **repo git sincronizado** entre máquinas: **nunca** colocar segredos lá.
- O AXON é instalado via venv editable; o **código** é resolvido pelo pacote instalado,
  então `AXON_ENGINE` controla só onde os **dados** vivem - o código não precisa se mover.
- O `.env` é carregado da **raiz do repo** (`C:\dev\axon\.env`), não de `AXON_ENGINE`.

## Decisões

| Tema | Decisão |
|---|---|
| Escopo | Full stack: estado + docs + **código** (busca semântica) |
| Topologia | **1 MCP global** + guidance global + captura/index **por-repo**, **store SQLite único** (decisões marcadas por repo → handoff cross-repo) |
| Provider (agora) | `free` profile (Groq + NVIDIA NIM). Resiliência multi-provider = Spec B |
| Automação de captura | **git hooks** (commit/push) + **guidance no CLAUDE.md** (sem SessionStart hook, sem custo fixo de tokens/sessão) |
| Disco | **Dados em D:** (`AXON_ENGINE=D:\axon`); código/venv ficam em `C:\dev\axon` |
| Segredos | `GROQ_API_KEY`/`NVIDIA_NIM_API_KEY` em `C:\dev\axon\.env` (gitignored); nunca no `~/.claude` |
| Runtime mode | `hybrid-local` (SQLite source-of-truth + Qdrant + roteamento cloud) |

## Componentes e mudanças

### 1. Infra (serviços sempre de pé)
- `docker compose up -d qdrant redis` (só esses dois do `docker-compose.yml` do AXON;
  `restart: unless-stopped` sobrevive reboot com Docker Desktop).
- Volume do Qdrant redirecionado pra **D:** via `docker-compose.override.yml` (gitignored)
  mapeando `D:\axon\qdrant` → `/qdrant/storage` (e Redis → `D:\axon\redis`).
- Postgres/langfuse/ollama ficam de fora.

### 2. Registro MCP global — `~/.claude/settings.json` → `mcpServers.axon`
```json
{
  "command": "C:\\Users\\samde\\dev\\axon\\.venv\\Scripts\\python.exe",
  "args": ["-m", "axon", "serve"],
  "env": {
    "AXON_ENGINE": "D:\\axon",
    "AXON_VAULT": "D:\\axon\\vault",
    "AXON_RUNTIME_MODE": "hybrid-local",
    "AXON_PROVIDER_PROFILE": "free",
    "AXON_EXPANSION_ENABLED": "false"
  }
}
```
Sem segredos aqui (keys no `.env`). O repo é detectado pelo cwd que o Claude Code passa
ao servidor por workspace.

### 3. Guidance no CLAUDE.md global — novo `~/.claude/axon/ROUTER.md`
@-importado pelo `~/.claude/CLAUDE.md` (mesmo padrão dos outros routers). Conteúdo (texto,
sem segredo):
- No início de trabalho num repo onboardado: chamar `axon_get_context` (recall) e/ou
  `axon_session_start`.
- Ao tomar uma decisão arquitetural/relevante: `axon_capture`.
- Para buscar: `search_code` / `get_adrs` / `get_dependencies`.
- Ao encerrar: `axon_session_end` com um resumo.
- Listar os repos onboardados (onde essas instruções valem).

### 4. Captura automática por git hooks (por-repo)
`axon install-hooks` em cada repo onboardado:
- `post-commit` → captura decisão (draft) do commit no SQLite.
- `pre-push` → judge + export de ADR/arquitetura pro vault.
- `post-merge`/`post-checkout` → revalida drafts de ADR.
- Nunca bloqueiam git (falha é engolida). Ficam em `.git/hooks` (não sincronizam).

### 5. Indexação de código (por-repo)
`axon init <repo>` (ou `axon index <repo>`) → símbolos no SQLite + embeddings no Qdrant.
Habilita `search_code`/`ask` sobre aquele repo.

### 6. Onboarding inicial
`axon init` + `axon install-hooks` na lista canônica de repos onboardados, que vive em
**`~/.claude/axon/ROUTER.md`** (fonte única - não re-copiar aqui). (Demais repos: on-demand depois.)

## Fluxo de dados

```
commit/push ─(git hook)→ decisão/ADR → SQLite (D:\axon\data\axon.db) ─(push)→ vault (D:\axon\vault)
sessão Claude ─ axon_get_context → lê SQLite (recall)
              ─ search_code/ask  → lê Qdrant (D:\axon\qdrant) + SQLite
classifier/judge/ask ─ nuvem (free profile: Groq/NIM)   [resto é 100% local]
```

## Unidades (isolamento)

- **Infra** (docker compose + override D:) — sobe/derruba Qdrant+Redis; independente.
- **Registro MCP** (settings.json) — expõe as tools; depende só do venv + env.
- **Guidance** (axon/ROUTER.md) — instrui o agente; texto puro, sem dependência de runtime.
- **Onboarding por-repo** (init + hooks) — captura+index de cada repo; idempotente, repetível.

Cada unidade é testável isolada (infra: `docker ps`; MCP: `axon_health`; guidance: lida pelo
Claude; onboarding: `axon_get_context`/`search_code` por repo).

## Verificação (end-to-end)

1. `docker compose ps` → qdrant + redis `Up`; storage em `D:\axon\qdrant`.
2. `axon doctor` verde (rtkx, caveman, SQLite em `D:\axon\data\axon.db`).
3. MCP: tools `axon_*`/`search_code`/`get_adrs` aparecem no Claude Code; `axon_health` ok.
4. Commitar num repo onboardado → `axon_get_context` retorna a decisão capturada.
5. `search_code "<símbolo conhecido>"` num repo indexado retorna o trecho (Qdrant).
6. Reabrir o projeto noutra sessão → `axon_get_context` recupera o estado anterior.

## Pré-requisitos do usuário
- Criar `D:\axon\` (a app cria subpastas data/vault/qdrant).
- Colocar `GROQ_API_KEY` e `NVIDIA_NIM_API_KEY` em `C:\dev\axon\.env`.
- Docker Desktop instalado e em auto-start.

## Fora de escopo (Spec B)
Cadeia de resiliência multi-provider (Cerebras + Ollama-cloud + fallback automático,
padronização em `gpt-oss-120b`/`gpt-oss-20b`). Não bloqueia A: captura/recall/código
funcionam sem ela; ela melhora `ask`/judge.
