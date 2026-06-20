# Design: `axon init` faz o onboarding completo (símbolos + embeddings)

Data: 2026-06-19
Status: aprovado (aguardando review do spec escrito)
Escopo: unificar a indexação de código no `axon init`. Sem auto-refresh por hook (fora de escopo).

## Contexto

Hoje o onboarding de um repo exige **dois comandos**:

- `axon init <repo>` → instala git hooks + `index_repo` (símbolos no **SQLite**, só `.py`/`.java`).
- `python -m axon.cli.pb index <repo> --ctx <ctx>` → `index_path` (embeddings no **Qdrant**,
  `.py`/`.java`/`.ts`/`.md`/`.txt`) + dep records no Redis.

Os dois populam stores **complementares**, não redundantes: o `search_code` faz o match
semântico no **Qdrant** e enriquece o hit com o subgrafo de símbolos do **SQLite**. Sem o
passo de embedding, o `search_code` não retorna nada. O `pb index` ser um passo manual
separado "deixou de fazer sentido" agora que o foco é evoluir o próprio AXON.

Fatos verificados no código:
- `axon.code.indexer.index_repo(repo, *, store)` → SQLite symbol nodes (`.py`/`.java`).
- `axon.embedder.pipeline.index_path(target, *, engine, store, vault_root, forced_ctx, graph_store, languages)`
  → embeddings no Qdrant por ctx + dep records no Redis. Já é **incremental** (hash-cache por
  arquivo) e exclui `node_modules`/`.venv`/`dist`/etc.
- `index_path` pula arquivos de ctx `work` **a menos que** `forced_ctx == "work"`.
- Embedder é **local** (fastembed `BAAI/bge-base-en-v1.5`, 768-dim, sem API key).
- O setup de `EmbedderEngine` + `VectorStore` + `GraphStore` está **duplicado** em ~5 comandos
  do `pb.py` (`index`, `index-dev`, etc.).

## Decisões

| Tema | Decisão |
|---|---|
| Escopo do `init` | `axon init` = hooks + símbolos SQLite + **embeddings Qdrant**, numa chamada. Refresh é manual (re-rodar `axon init`). |
| ctx padrão | `--ctx knowledge` (default). Código = base de conhecimento; está no conjunto de busca default. |
| ctx `work` | **Só** quando `--ctx work` é passado explicitamente. O default nunca escreve em `work`. |
| Abordagem | Extrair helper único `embed_repo(...)` consumido por `init` **e** `pb`; remove a duplicação do `pb.py`. |
| Degradação | Qdrant fora → `init` não falha: instala hooks + símbolos e avisa que embeddings foram pulados. Redis fora → dep records pulados. |
| Migração | Re-indexar os 9 repos onboardados de `personal` → `knowledge` e limpar a collection `personal`. |
| Fora de escopo | Auto-refresh do índice por git hook no commit. `pb index`/`index-dev` continuam existindo (uso de vault/manifesto). |

## Componentes e mudanças

### 1. Novo helper isolado — `axon/code/embedder.py`
```
async def embed_repo(
    repo_path: Path | str,
    *,
    ctx: str = "knowledge",
    engine: EmbedderEngine | None = None,
    store: VectorStore | None = None,
    graph_store: GraphStore | None = None,
) -> tuple[int, int]:  # (indexed_files, total_chunks)
```
- Responsabilidade única: embeddar um repo num ctx. Monta engine/store/graph_store se não
  forem injetados (default: runtime config), chama `ensure_collections()` e
  `index_path(repo_path, engine=..., store=..., vault_root=..., forced_ctx=ctx, graph_store=...)`.
- `forced_ctx=ctx` faz todo arquivo ir pro ctx escolhido (o `infer_ctx_from_path` não decide).
- Fecha store/graph_store no fim (`finally`).
- Testável isolado: injeta mocks, conta chunks por ctx, valida idempotência (hash-cache).

### 2. `axon init` (em `axon/__main__.py`)
- Nova opção: `--ctx` (default `"knowledge"`).
- Fluxo: `install_hooks` → `index_repo` (símbolos) → `embed_repo(repo, ctx=ctx)` (embeddings).
- Saída agregada:
  ```
  hooks installed: post-commit, pre-push, post-merge, post-checkout
  indexed N symbols from <repo>
  embedded M chunks into ctx=knowledge
  ```
- Degradação: o passo de embedding é envolvido de modo que falha de Qdrant/Redis vire um aviso
  (`embeddings skipped (<motivo>)`), sem abortar o `init` nem mudar o exit code para os passos
  que deram certo.

### 3. Refactor do `pb.py` (DRY, escopo contido)
- `pb index` e `pb index-dev` passam a chamar `embed_repo`/`index_path` via o mesmo caminho do
  helper, eliminando a montagem duplicada de engine/store/graph_store. Comportamento externo
  inalterado (mesmos flags, mesma saída).

### 4. Migração one-shot (parte da entrega, não código permanente)
- Re-indexar os 9 repos onboardados com `ctx=knowledge`.
- Limpar a collection `personal` (hoje só contém o código que foi indexado na integração).
- Verificar: `search_code` (sem ctx) retorna hits vindos de `knowledge`.

## Fluxo de dados (depois)

```
axon init <repo> ─ install_hooks ─→ .git/hooks/*
                 ─ index_repo     ─→ SQLite symbol graph (.py/.java)
                 ─ embed_repo     ─→ Qdrant (ctx=knowledge) + Redis dep records (.py/.java/.ts/.md/.txt)
search_code <q>  ─ embed(q) → Qdrant(knowledge,...) → enrich via SQLite subgraph
```

## Unidades (isolamento)

- **embed_repo** (`axon/code/embedder.py`) — embedda um repo num ctx; depende de
  engine/store/graph_store; testável com mocks.
- **init** (`axon/__main__.py`) — orquestra hooks + símbolos + embeddings; depende de
  `install_hooks`, `index_repo`, `embed_repo`.
- **pb index/index-dev** — passam a delegar ao mesmo caminho; sem mudança de interface.

## Verificação (end-to-end)

1. `axon init <repo-py>` num repo limpo → imprime os 3 passos; `embedded M chunks` com M>0.
2. Qdrant parado → `axon init` ainda instala hooks + símbolos e avisa `embeddings skipped`; exit 0.
3. `search_code "<símbolo conhecido>"` retorna o trecho do repo recém-`init`ado (ctx default).
4. Re-rodar `axon init` no mesmo repo sem mudanças → `embedded 0 chunks` (hash-cache).
5. `--ctx work` → embeddings vão pra collection `work` e **não** aparecem no `search_code` sem ctx.
6. Migração: collection `personal` vazia/removida; os 9 repos buscáveis via `knowledge`.

## Testes

- Unit `embed_repo`: conta chunks, ctx correto, idempotência (hash-cache), store/graph fechados.
- Unit/integração `init`: os 3 passos rodam; caminho "qdrant fora não quebra" (mock que levanta).
- Regressão `pb index`: saída/flags inalterados após o refactor.
- Cobertura alvo: 80%+ nas unidades novas/alteradas.

## Fora de escopo
- Auto-refresh do índice por git hook no commit (re-index incremental automático).
- Remover ou renomear `pb index`/`index-dev`.
- Mudar o conjunto de linguagens suportadas pelo embedder.
