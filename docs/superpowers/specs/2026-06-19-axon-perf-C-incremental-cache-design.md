# Design: Cache incremental persistente - tabela `file_index` no SQLite (Pilar C)

Data: 2026-06-19
Status: rascunho - aguardando gate de medicao (Phase 0) antes de implementar
Escopo: persistir hashes de arquivo em SQLite para skip cross-process; reconciliar pontos
Qdrant por arquivo (delete+re-add) em vez de por chunk-id; pipelinear upserts Redis;
definir locking de concorrencia; migrar one-shot os 9 repos ja indexados.

Este spec cobre o **Pilar C** do overhaul de performance do AXON (linear, cacheavel,
paralelo). O Pilar A trata cap de chunks/chunker; o Pilar B trata aceleracao de embedding
via providers onnxruntime. Este pilar e o pre-requisito dos outros dois: sem cache
persistente, nenhuma execucao incremental e possivel, e sem reconcile por arquivo, pontos
orfaos acumulam a cada re-index.

---

## Contexto

### Problema raiz

O `_FILE_HASH_CACHE` atual (`pipeline.py:28`) e um dict em memoria, escopo de processo:

```python
# pipeline.py:28
_FILE_HASH_CACHE: dict[str, str] = {}
```

Consequencia: toda nova invocacao do indexer (hook post-commit, `axon init`, `pb index`)
recalcula e re-embeda **todos** os arquivos do repo, mesmo que 0 linhas tenham mudado.
Para os 9 repos ja indexados, isso significa minutos de CPU + I/O Qdrant a cada hook.

### Instabilidade do chunk-id

O `_chunk_id` e derivado de `uuid5(path::symbol::start_line)` (`pipeline.py:206-211`):

```python
# pipeline.py:206-211
def _chunk_id(file_path: Path, chunk: Chunk) -> uuid.UUID:
    key = f"{file_path}::{chunk.symbol}::{chunk.start_line}"
    return uuid.uuid5(uuid.NAMESPACE_URL, key)
```

Editar 3 linhas acima de um simbolo desloca `start_line` de todos os chunks abaixo -
todos os IDs mudam - os pontos antigos ficam orfaos no Qdrant. O upsert atual nao deleta
os pontos do arquivo antes de reinserir; apenas faz `upsert` com novos IDs, deixando os
pontos velhos acumulados.

Verificacao empirica necessaria antes de implementar (ver ledger de hipoteses abaixo):
rolar `client.scroll()` para um arquivo editado e confirmar que o count aumenta em vez de
manter-se estavel.

### Redis sequencial nao-pipelinado

O loop de `upsert_deps` em `pipeline.py:196-202`:

```python
# pipeline.py:197-202
for record in dep_records:
    await graph_store.upsert_deps(record)
```

Cada `upsert_deps` dispara **um** `hset` Redis (`graph_store.py:34-46`). Para 100 simbolos
com latencia de 1 ms/round-trip = minimo de 100 ms sequenciais. Redis suporta pipelining
nativo; nao esta sendo usado.

### Infraestrutura de migracao SQLite ja existente

O `SessionStore` ja usa migrations `.sql` em ordem alfabetica, rastreadas em
`schema_version` (`session_store.py:44-62`). Adicionar `003_file_index.sql` e adicionado
sem mudanca de codigo - o `_apply_migrations()` ja lida com novos arquivos.

Fatos verificados no codigo:
- `session_store.py:44-62` - `_apply_migrations()` le migrations de `store/migrations/`,
  compara com `schema_version`, executa apenas as novas.
- `session_store.py:109-112` - WAL mode + `busy_timeout=5000` + `synchronous=NORMAL` ja
  configurados.
- `store/migrations/000_baseline.sql` - tabelas base: `adr`, `session_memory`, etc.
- `store/migrations/001_axon_graph.sql` - tabelas de grafo: `nodes`, `edges`, `sessions`.
- `store/migrations/002_unique_edges.sql` - dedup de edges, index `ux_edges_triple`.
- `pipeline.py:59-75` - `iter_supported_files` usa `rglob('*')` sem poda de dirs.
- `vector_store.py:93-114` - `upsert_batch` agrupa por ctx, um upsert Qdrant por ctx.
- `graph_store.py:34-46` - `upsert_deps` = um `hset` Redis por simbolo.
- `session_store.py:101` - `asyncio.Lock` ja presente para serializar acesso ao SQLite.

---

## Ledger de hipoteses (verificar barato antes de implementar)

| # | Hipotese | Verificacao barata | Onde registrar |
|---|---|---|---|
| H1 | Pontos orfaos ja existem nos 9 repos indexados | `client.scroll()` antes e depois de editar 1 arquivo; checar se count sobe | `benchmarks/phase0_baseline.json` |
| H2 | `_FILE_HASH_CACHE` in-memory e a causa do re-embed total a cada processo | Logar hash-hits vs misses num `axon init` em repo ja indexado; se 0 hits, confirma | log de debug temporario |
| H3 | Redis loop sequential adiciona latencia mensuravel (>100 ms para 100+ simbolos) | `perf_counter()` ao redor do loop em `pipeline.py:197-202` num repo de 200+ simbolos | `benchmarks/phase0_profile.json` |
| H4 | O `rglob` sem poda nao e o gargalo principal de wall time (embedding domina) | `time` em `iter_supported_files()` isolado vs wall time total de `index_path()` | `benchmarks/phase0_profile.json` |
| H5 | Colisoes de `uuid5` nao ocorrem hoje nos 9 repos | Script de varredura de IDs duplicados; resultado esperado: dict vazio | verificacao unica pre-deploy |

**Nenhuma hipotese pode ser declarada como fato no plano de implementacao ate ser medida.**
O gate de Phase 0 (definido abaixo) e o controle.

---

## Decisoes

| Tema | Decisao | Racional |
|---|---|---|
| Armazenamento do cache de hash | Tabela `file_index` no SQLite existente (mesmo DB do `SessionStore`) | Reutiliza infra, migrations, locking e WAL ja configurados; zero nova dependencia |
| Schema da tabela | `file_path TEXT PK, sha1 TEXT, chunk_count INTEGER, indexed_at TEXT, ctx TEXT` | Minimo necessario para skip, reconcile e auditoria; sem over-engineering |
| Versao de schema | Migration `003_file_index.sql` na pasta `store/migrations/` | O `_apply_migrations()` existente executa sem mudanca de codigo |
| Reconcile Qdrant | Delete-all-for-file + re-add, nunca diff por chunk-id | Chunk-ids sao instave is (start_line muda); delete-then-upsert e O(n_chunks) mas correto |
| Escopo do delete | Delete por `(collection, file_path)` via `filter` Qdrant | Evita scroll e coleta de IDs; um `delete` por arquivo alterado |
| Redis pipelining | Batch de N `hset` num unico pipeline por `flush_batch` | `redis-py` suporta `pipe()` nativo; mudanca de 3 linhas, ganho proporcional a N simbolos |
| Locking de concorrencia | `asyncio.Lock` ja presente no `SessionStore`; adquirir antes de ler/escrever `file_index` | Hook e index manual compartilham o mesmo processo async ou processos diferentes; SQLite WAL + `busy_timeout=5000` cobre o caso multi-processo |
| Walk de arquivos | Substituir `rglob` por `git ls-files --cached --others --exclude-standard` como fonte primaria, com fallback para `rglob` se nao for git repo | Exclui arquivos gitignored (`.env`, `secrets.json`) automaticamente; ja usado em `indexer.py:78-85` |
| Seguranca/privacidade | Arquivos listados em `.gitignore` jamais sao embeddados | `git ls-files` nao lista arquivos ignorados; teste de seguranca obrigatorio |
| Migracao one-shot | Reindex completo dos 9 repos + purge de pontos com `ctx=personal` | Limpa orfaos acumulados; alinha com a decisao do Spec A (ctx=knowledge) |
| GPU/multiprocessing | Fora de escopo deste pilar | Pilar B (providers onnxruntime) e sequencial a este; YAGNI ate medicao provar necessidade |

---

## Componentes e mudancas

### 1. Migration `003_file_index.sql`

Arquivo novo em `C:/Users/samde/dev/axon/src/axon/store/migrations/003_file_index.sql`:

```sql
-- 003_file_index.sql
-- Cache persistente de hashes por arquivo para skip incremental cross-process.
-- Requer: 000_baseline, 001_axon_graph, 002_unique_edges ja aplicados.

CREATE TABLE IF NOT EXISTS file_index (
    file_path  TEXT    NOT NULL,
    ctx        TEXT    NOT NULL,
    sha1       TEXT    NOT NULL,
    chunk_count INTEGER NOT NULL DEFAULT 0,
    indexed_at TEXT    NOT NULL,  -- ISO-8601 UTC
    PRIMARY KEY (file_path, ctx)
);

CREATE INDEX IF NOT EXISTS ix_file_index_ctx
    ON file_index (ctx);
```

Notas de design:
- PK composta `(file_path, ctx)` porque o mesmo arquivo pode ser indexado em contextos
  diferentes (ex: `knowledge` e `work`).
- `chunk_count` permite validar se o numero de chunks mudou sem precisar ler o Qdrant.
- `CREATE TABLE IF NOT EXISTS` garante idempotencia (re-aplicacao segura).
- Nao ha rollback automatico; se a migration falhar a meio, o `_apply_migrations()` nao
  registra o nome na `schema_version` e tentara re-executar na proxima inicializacao
  (o `IF NOT EXISTS` protege contra criacao dupla de tabela).

**Nenhuma mudanca de codigo e necessaria para aplicar esta migration** - o
`_apply_migrations()` em `session_store.py:44-62` la detecta `003_file_index.sql` e
executa na proxima inicializacao do `SessionStore`.

### 2. Modulo `axon/store/file_cache.py` (novo)

Responsabilidade unica: ler e escrever `file_index`. Isola toda a logica de cache do
`pipeline.py`.

```python
# axon/store/file_cache.py
from __future__ import annotations
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

class FileCache(Protocol):
    async def get_sha1(self, file_path: str, ctx: str) -> str | None: ...
    async def set_entry(
        self, file_path: str, ctx: str, sha1: str, chunk_count: int
    ) -> None: ...
    async def delete_entry(self, file_path: str, ctx: str) -> None: ...
    async def list_entries(self, ctx: str) -> list[tuple[str, str]]: ...
    # Retorna lista de (file_path, sha1) para o ctx dado

class SqliteFileCache:
    """Implementacao concreta usando a conexao aiosqlite do SessionStore."""
    def __init__(self, conn, lock):  # aiosqlite.Connection, asyncio.Lock
        self._conn = conn
        self._lock = lock

    async def get_sha1(self, file_path: str, ctx: str) -> str | None:
        async with self._lock:
            cur = await self._conn.execute(
                "SELECT sha1 FROM file_index WHERE file_path=? AND ctx=?",
                (file_path, ctx),
            )
            row = await cur.fetchone()
            return row[0] if row else None

    async def set_entry(
        self, file_path: str, ctx: str, sha1: str, chunk_count: int
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        async with self._lock:
            await self._conn.execute(
                """
                INSERT INTO file_index (file_path, ctx, sha1, chunk_count, indexed_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (file_path, ctx) DO UPDATE SET
                    sha1        = excluded.sha1,
                    chunk_count = excluded.chunk_count,
                    indexed_at  = excluded.indexed_at
                """,
                (file_path, ctx, sha1, chunk_count, now),
            )
            await self._conn.commit()

    async def delete_entry(self, file_path: str, ctx: str) -> None:
        async with self._lock:
            await self._conn.execute(
                "DELETE FROM file_index WHERE file_path=? AND ctx=?",
                (file_path, ctx),
            )
            await self._conn.commit()

    async def list_entries(self, ctx: str) -> list[tuple[str, str]]:
        async with self._lock:
            cur = await self._conn.execute(
                "SELECT file_path, sha1 FROM file_index WHERE ctx=?", (ctx,)
            )
            return await cur.fetchall()

def sha1_of_file(path: Path) -> str:
    h = hashlib.sha1(usedforsecurity=False)
    h.update(path.read_bytes())
    return h.hexdigest()
```

Dependencias: `aiosqlite` (ja em uso), `asyncio.Lock` (ja no `SessionStore`).
Sem nova dependencia de terceiros.

### 3. Alteracoes em `pipeline.py`

#### 3a. Substituir `_FILE_HASH_CACHE` por `FileCache`

Remover `pipeline.py:28`:
```python
# REMOVER:
_FILE_HASH_CACHE: dict[str, str] = {}
```

Adicionar `file_cache: FileCache | None = None` como parametro opcional de `index_path`.
Quando `None`, cai para comportamento atual (sem skip - modo forca total). Quando
fornecido, consulta `get_sha1` antes de processar cada arquivo.

#### 3b. Logica de skip incremental

```python
# Pseudocodigo - posicao: antes do bloco de chunking em index_path()
current_sha1 = sha1_of_file(file_path)
if file_cache:
    cached = await file_cache.get_sha1(str(file_path), ctx)
    if cached == current_sha1:
        stats["skipped"] += 1
        continue  # arquivo nao mudou - pular
```

#### 3c. Substituir `git ls-files` por `rglob` em `iter_supported_files`

Alterar `pipeline.py:59-75` para usar `git ls-files` como fonte primaria:

```python
# Pseudocodigo - iter_supported_files
import subprocess

def iter_supported_files(target: Path, excluded_dirs=EXCLUDED_DIR_NAMES):
    try:
        result = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=target, capture_output=True, text=True, check=True,
        )
        for line in result.stdout.splitlines():
            p = target / line.strip()
            if p.suffix in _LANGUAGE_MAP and p.is_file():
                yield p
    except (subprocess.CalledProcessError, FileNotFoundError):
        # fallback: rglob com poda manual (repo nao-git)
        for p in target.rglob("*"):
            if any(part in excluded_dirs for part in p.parts):
                continue
            if p.suffix in _LANGUAGE_MAP:
                yield p
```

Esta mudanca e **obrigatoria** pelo requisito de seguranca: arquivos em `.gitignore`
(ex: `.env`, `secrets.json`) nao podem ser embeddados. `git ls-files` ja exclui
arquivos ignorados sem nenhuma logica adicional.

#### 3d. Reconcile Qdrant por arquivo (delete-then-upsert)

Antes de chamar `store.upsert_batch()` para um arquivo modificado (sha1 diferente),
deletar todos os pontos do arquivo naquele ctx:

```python
# Pseudocodigo - posicao: apos detectar sha1 mudou, antes de chunk/embed
if file_cache and old_sha1 is not None:
    # arquivo existia - deletar pontos orfaos antes de reinserir
    await store.delete_file_points(file_path=str(file_path), ctx=ctx)
```

O metodo `delete_file_points` a adicionar em `vector_store.py`:

```python
# vector_store.py - novo metodo
async def delete_file_points(self, file_path: str, ctx: str) -> None:
    collection = self._collection_for_ctx(ctx)
    await self._client.delete(
        collection_name=collection,
        points_selector=models.FilterSelector(
            filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="file_path",
                        match=models.MatchValue(value=file_path),
                    )
                ]
            )
        ),
    )
```

Depois do upsert bem-sucedido, atualizar o cache:
```python
await file_cache.set_entry(str(file_path), ctx, current_sha1, len(chunks))
```

#### 3e. Deteccao de arquivos deletados

Apos o walk de arquivos, comparar a lista de arquivos encontrados com `list_entries(ctx)`
do cache. Arquivos no cache mas ausentes no walk foram deletados:

```python
# Pseudocodigo - ao final de index_path(), antes de retornar
if file_cache:
    found_paths = {str(p) for p in iterated_files}
    cached_entries = await file_cache.list_entries(ctx)
    for cached_path, _ in cached_entries:
        if cached_path not in found_paths:
            await store.delete_file_points(file_path=cached_path, ctx=ctx)
            await file_cache.delete_entry(cached_path, ctx)
            stats["deleted"] += 1
```

### 4. Redis pipelining em `pipeline.py` e `graph_store.py`

#### 4a. Assinatura atual (sequencial)

```python
# pipeline.py:197-202 - ATUAL
for record in dep_records:
    await graph_store.upsert_deps(record)  # 1 hset por iteracao
```

#### 4b. Novo metodo `upsert_deps_batch` em `graph_store.py`

```python
# graph_store.py - novo metodo
async def upsert_deps_batch(self, records: list[DependencyRecord]) -> None:
    if not records:
        return
    async with self._redis.pipeline(transaction=False) as pipe:
        for record in records:
            key = f"dep:{record.symbol}"
            pipe.hset(key, mapping={
                "calls": json.dumps(record.calls),
                "called_by": json.dumps(record.called_by),
            })
        await pipe.execute()
```

`transaction=False` evita o overhead do `MULTI/EXEC` para upserts sem necessidade de
atomicidade entre simbolos. Se atomicidade for necessaria no futuro, trocar para `True`.

#### 4c. Chamada no `pipeline.py`

```python
# pipeline.py - substituir loop sequencial
await graph_store.upsert_deps_batch(dep_records)
```

Verificacao de ganho: medir `perf_counter()` ao redor do loop atual num repo de 200+
simbolos antes de deployar (hipotese H3 no ledger). Se o ganho for < 20 ms, o pipeline
continua valendo pela corretude (atomicidade futura) mas nao e urgente.

### 5. Locking e concorrencia

#### Cenario de risco

O git hook (`python -m axon.hooks.git_event post-commit`) e um `axon index` manual podem
ser disparados em paralelo - processos separados, mesmo repo.

#### Camadas de protecao

| Camada | Mecanismo | Cobre |
|---|---|---|
| SQLite WAL | `journal_mode=WAL` + `busy_timeout=5000` (`session_store.py:109-112`) | Dois processos lendo/escrevendo `file_index` simultaneamente |
| `asyncio.Lock` | Lock existente no `SessionStore.__init__:101`, repassado ao `SqliteFileCache` | Coroutines concorrentes no mesmo processo |
| Qdrant | Qdrant aceita upserts e deletes concorrentes sem corrupcao de dados | Duplicate-upsert possivel; resolvido pelo reconcile por arquivo |
| Arquivo `.axon/index.lock` | Lockfile simples (open + `O_EXCL`) adquirido no inicio de `index_path()`, removido no `finally` | Impede dois processos de indexar o mesmo repo simultaneamente |

O lockfile e a garantia mais forte para o caso multi-processo:

```python
# axon/store/index_lock.py - novo modulo
import os
from contextlib import asynccontextmanager
from pathlib import Path

class IndexLockError(Exception):
    pass

@asynccontextmanager
async def acquire_index_lock(repo_root: Path):
    lock_path = repo_root / ".axon" / "index.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
    except FileExistsError:
        raise IndexLockError(
            f"Outro processo esta indexando {repo_root}. "
            f"Se o processo anterior travou, remova: {lock_path}"
        )
    try:
        yield
    finally:
        lock_path.unlink(missing_ok=True)
```

O hook post-commit nunca deve bloquear o git. Se o lockfile existir, o hook loga um aviso
e sai com exit 0 (como os demais erros em `git_installer.py:41`). A indexacao sera feita
no proximo commit ou via `axon index` manual.

### 6. Migracao one-shot dos 9 repos ja indexados

#### Contexto

Os 9 repos foram indexados com a logica antiga (sem `file_index`, possivelmente com ctx
`personal`). Apos deployar este pilar, o cache SQLite estara vazio para todos eles -
o proximo `axon index` reindexara tudo do zero, o que e correto.

O problema e que pontos orfaos ja existem no Qdrant (hipotese H1). O reindex sem purge
apenas adiciona novos pontos por cima dos velhos.

#### Procedimento

```bash
# Passo 1 - purge de colecoes com ctx antigos (personal, outros ctx temporarios)
# Executar UMA VEZ antes de deployar o codigo novo
python - <<'EOF'
from qdrant_client import QdrantClient
client = QdrantClient("http://localhost:6333")
# Listar colecoes existentes e verificar quais sao ctx legados
for col in client.get_collections().collections:
    print(col.name)
EOF

# Passo 2 - para cada colecao legada confirmada, deletar
# (substituir "personal" pelo nome real se diferente)
python - <<'EOF'
from qdrant_client import QdrantClient
client = QdrantClient("http://localhost:6333")
client.delete_collection("personal")  # ajustar se necessario
EOF

# Passo 3 - reindex completo dos 9 repos com o codigo novo deployado
# O file_index estara vazio, entao todo arquivo sera processado
axon index <vault_root> --ctx knowledge

# Passo 4 - verificar ausencia de orfaos pos-reindex
python - <<'EOF'
from qdrant_client import QdrantClient
client = QdrantClient("http://localhost:6333")
result, _ = client.scroll("knowledge", limit=10000, with_payload=True)
paths = {p.payload.get("file_path") for p in result}
# Inspecionar manualmente se algum path e inesperado
print(f"Total pontos: {len(result)}")
print(f"Paths distintos: {len(paths)}")
EOF
```

O reindex completo e necessario uma unica vez. Apos isso, o `file_index` tem estado
correto e todos os refreshes subsequentes serao incrementais.

---

## Fluxo de dados (apos este pilar)

```
axon index <repo> --ctx knowledge
    |
    +-- acquire_index_lock(repo_root)   # impede concorrencia multi-processo
    |
    +-- iter_supported_files(repo)      # git ls-files (exclui gitignored)
    |   para cada arquivo:
    |     sha1 = sha1_of_file(path)
    |     cached_sha1 = await file_cache.get_sha1(path, ctx)
    |     if sha1 == cached_sha1:  SKIP
    |     else:
    |       await store.delete_file_points(path, ctx)   # reconcile Qdrant
    |       chunks = chunk_source(content, language, path)
    |       pending_batch.extend(chunks)
    |       if len(pending_batch) >= _BATCH_SIZE:
    |         await _flush_batch(pending_batch, engine, store, ctx)
    |         pending_batch.clear()
    |
    +-- _flush_batch (ultimo batch)
    |
    +-- build_dependency_records(graph_chunks)   # 2a parse - fora de escopo deste pilar
    +-- await graph_store.upsert_deps_batch(dep_records)  # pipelinado
    |
    +-- deteccao de arquivos deletados (list_entries vs found_paths)
    |   para cada deletado: delete_file_points + delete_entry
    |
    +-- release_index_lock()
```

---

## Gate de Phase 0 (pre-requisito de implementacao)

**Nenhuma linha de codigo deste pilar pode ser mergeada antes que todas as condicoes
abaixo sejam satisfeitas e registradas em `benchmarks/phase0_baseline.json`.**

| Condicao | Metrica alvo | Como medir |
|---|---|---|
| Throughput baseline capturado | Registrar chunks/s no corpus sintetico de 500 funcoes | `time index_path()` em corpus fixo |
| Peak RSS baseline capturado | Registrar MB nos 9 repos | `psutil` amostrado a cada 2 s |
| H1 verificada | Confirmar se orfaos existem hoje | `scroll()` antes/depois de editar 1 arquivo |
| H3 verificada | Medir latencia do loop Redis em 200+ simbolos | `perf_counter()` ao redor de `pipeline.py:197-202` |
| H4 verificada | Medir wall time do `rglob` isolado vs total | `time iter_supported_files()` isolado |
| GPU disponivel (Pilar B) | `bool` em `phase0_baseline.json` | `ort.get_available_providers()` |
| Recall baseline >= 0.80 | Top-1 e Top-3 no golden set de 20 queries | Harness de recall (ver abaixo) |

Se o peak RSS exceder 8 GB durante a medicao baseline, o streaming de
`build_dependency_records` (processar por arquivo em vez de acumular todos os chunks)
vira item 0 da implementacao, antes de qualquer outro trabalho de perf.

---

## Guard de recall/qualidade

Este pilar nao toca o chunker nem o embedder diretamente, mas o reconcile por arquivo
(delete-then-upsert) pode, em hipotese, alterar quais pontos estao no Qdrant. O guard de
recall e obrigatorio antes e depois do deploy.

### Conjunto golden (fixo, 20 queries)

Arquivo: `tests/recall/golden_set.json` (criado manualmente, nunca auto-gerado).

Distribuicao:
- 8 queries Python (funcao, metodo, utilitario curto)
- 5 queries Java (classe, metodo de interface, enum)
- 4 queries TypeScript (funcao, arrow function, tipo exportado)
- 3 queries cross-file/arquiteturais

Cada entrada:
```json
{
  "query": "string de busca semantica",
  "expected_file": "caminho/absoluto/normalizado.py",
  "expected_symbol": "nome_da_funcao_ou_classe",
  "min_score": 0.70
}
```

### Metricas de gate

| Metrica | Alvo |
|---|---|
| Top-1 hit rate (hits[0].file_path == expected) | >= 0.90 |
| Top-3 hit rate (expected em hits[0..2]) | >= 0.95 |
| Score geral (BenchmarkRunSummary.score) | >= 0.90 |

Qualquer regressao vs `tests/recall/baseline.json` bloqueia o merge.

### Estabilidade cross-plataforma

As mesmas queries e expected_files devem passar em R7 5800X3D e M1 Pro. A diferenca de
modelo (bge-base 768-dim no desktop, bge-small 384-dim no mac) e mitigada pelo threshold
`min_score >= 0.70` (folga suficiente para variacao de modelo). Se um par
query/expected_file falhar no mac, o golden set precisa ser revisado antes de deployar.

---

## Criterios de sucesso mensuravelmente (por maquina)

| Metrica | R7 5800X3D | M1 Pro | Como medir |
|---|---|---|---|
| Wall time index completo (9 repos, cache frio) | <= 5 min | <= 8 min | `time axon index <vault>` com cache vazio; mediana de 3 runs |
| Wall time refresh incremental (1 arquivo, 10-50 chunks) | <= 10 s | <= 15 s | 5 arquivos de tamanhos variados (10/20/30/40/50 chunks); todos devem passar |
| Wall time hook post-commit (20 arquivos alterados) | <= 30 s | <= 45 s | `python -m axon.hooks.git_event post-commit` cronometrado; maximo de 3 runs |
| Peak RSS index completo (9 repos) | <= 2 GB | <= 1.5 GB | `psutil.Process().memory_info().rss` amostrado a cada 2 s |
| Throughput embedding (chunks/s end-to-end) | >= 300 chunks/s | >= 200 chunks/s | corpus sintetico fixo de 500 funcoes Python (15-30 linhas cada) |
| Recall Top-1 (golden set 20 queries) | >= 0.90 | >= 0.90 | harness em Qdrant real com corpus de referencia (`src/axon/embedder/`, `src/axon/store/`) |
| Recall Top-3 (golden set 20 queries) | >= 0.95 | >= 0.95 | mesmo harness |
| Exclusao de arquivos gitignored | 0 pontos cujo file_path bate `.gitignore` | 0 pontos | scroll Qdrant pos-index em repo com `.env` e `secrets.json` gitignored |
| Orphan-free pos-reconcile | 0 pontos orfaos apos editar 3 linhas acima de simbolo | 0 pontos orfaos | scroll por file_path antes e depois; count deve ser igual (nao acumular) |
| Seguranca de concorrencia | 0 corrupcoes em 20 trials de index+hook simultaneos | 0 corrupcoes | 2 processos em paralelo via subprocess; scroll pos-execucao; sem IDs duplicados nem JSON invalido no Redis |

---

## Unidades (isolamento e testabilidade)

| Modulo | Responsabilidade | Dependencias injetaveis |
|---|---|---|
| `axon/store/file_cache.py::SqliteFileCache` | CRUD na `file_index`; calculo de sha1 | `aiosqlite.Connection`, `asyncio.Lock` |
| `axon/store/index_lock.py::acquire_index_lock` | Lockfile atomico por repo | `Path` (repo root) |
| `axon/store/graph_store.py::upsert_deps_batch` | Batch pipeline Redis | `redis.asyncio.Redis` |
| `axon/embedder/vector_store.py::delete_file_points` | Delete Qdrant por (file_path, ctx) | `QdrantClient` |
| `axon/embedder/pipeline.py::index_path` (modificado) | Orquestra skip, reconcile, flush, delete-orfaos | `FileCache`, `VectorStore`, `GraphStore`, `EmbedderEngine` |
| `axon/store/migrations/003_file_index.sql` | Schema da tabela | n/a - SQL puro |

Cada unidade e testavel com mocks injetados:
- `SqliteFileCache`: testar `get_sha1` miss/hit, `set_entry` UPSERT, `delete_entry`.
- `acquire_index_lock`: testar lock adquirido, lock ja existente (levanta `IndexLockError`), release no `finally`.
- `upsert_deps_batch`: mock do `pipeline()` Redis; verificar que o numero correto de `hset` e chamado sem `await` individual.
- `delete_file_points`: mock do `QdrantClient.delete`; verificar o filtro `file_path`.
- `index_path` com `FileCache` mockado: verificar que arquivos com sha1 identico sao pulados; arquivos alterados passam por delete-then-upsert.

---

## Verificacao end-to-end

1. **Skip incremental:** indexar um repo; sem modificar nenhum arquivo, re-rodar `axon index`;
   verificar que o output e `0 arquivos re-embeddados` (todos pulados pelo cache).

2. **Reconcile orfao:** indexar um arquivo Python de 5 funcoes; editar 3 linhas antes da
   primeira funcao (desloca `start_line` de todas as funcoes); re-indexar; scroll Qdrant
   para esse `file_path`; count deve ser 5 (nao 10).

3. **Arquivo deletado:** indexar repo; deletar 1 arquivo; re-indexar; scroll Qdrant para
   o `file_path` deletado deve retornar 0 pontos. `file_index` nao deve conter a entrada.

4. **Gitignore guard:** criar `.env` no repo com `SECRET=abc`; adicionar ao `.gitignore`;
   `axon index`; scroll Qdrant por `file_path` contendo `.env`; deve retornar 0 resultados.

5. **Concorrencia:** lancar `axon index <repo>` e `python -m axon.hooks.git_event post-commit`
   via `subprocess` simultaneamente; apos ambos terminarem, scroll Qdrant e verificar
   ausencia de IDs duplicados; checar Redis por JSON invalido em chaves `dep:*`. Repetir
   20 vezes.

6. **Lockfile:** durante um `axon index` em andamento (artificialmente lentificado via
   sleep em teste de integracao), tentar um segundo `axon index` no mesmo repo; o segundo
   deve sair com aviso `outro processo indexando` e exit 0 (sem stacktrace).

7. **Migracao one-shot:** apos purge das colecoes legadas e reindex completo, executar
   `axon search_code "funcao conhecida"`; deve retornar hits dos 9 repos em ctx `knowledge`.

---

## Testes

### Unitarios

- `test_file_cache.py`:
  - `test_get_sha1_miss`: arquivo nao no cache -> `None`.
  - `test_get_sha1_hit`: arquivo no cache -> sha1 correto.
  - `test_set_entry_upsert`: segunda chamada com sha1 diferente atualiza a linha.
  - `test_delete_entry`: entrada removida, `get_sha1` retorna `None`.
  - `test_list_entries_filters_by_ctx`: entries de ctx `work` nao aparecem em `knowledge`.

- `test_index_lock.py`:
  - `test_acquire_releases_on_exit`: lockfile removido apos bloco.
  - `test_acquire_raises_if_locked`: segundo `acquire` levanta `IndexLockError`.
  - `test_acquire_releases_on_exception`: lockfile removido mesmo com excecao interna.

- `test_upsert_deps_batch.py`:
  - `test_batch_single_pipeline_call`: N simbolos resultam em exatamente 1 `pipe.execute()`.
  - `test_empty_batch_no_op`: lista vazia nao chama `pipeline()`.

- `test_delete_file_points.py`:
  - `test_filter_contains_file_path`: verifica que o filtro Qdrant inclui o campo `file_path`.
  - `test_correct_collection_selected`: `ctx=work` usa a collection correta.

### Integracao

- `test_incremental_skip.py`: index + re-index sem mudancas; mock do embedder; assertar
  que `engine.embed()` nao foi chamado na segunda rodada.
- `test_orphan_reconcile.py`: index -> editar arquivo -> re-index -> scroll Qdrant;
  count identico antes/depois (nao acumula).
- `test_deleted_file_cleanup.py`: index -> deletar arquivo -> re-index -> scroll = 0.
- `test_gitignore_exclusion.py`: arquivo em `.gitignore` -> index -> scroll = 0.
  (TESTE DE SEGURANCA - obrigatorio, nao pode ser pulado por coverage.)

### Regressao de recall

- `test_recall_guard.py`: carrega `tests/recall/baseline.json`; roda harness de 20 queries
  contra Qdrant real (testcontainers); `compare_benchmark_runs(current, baseline)`;
  `assert len(report.regressions) == 0` e `assert summary.score >= 0.90`.

### Cobertura

Minimo 80% nos modulos novos/alterados: `file_cache.py`, `index_lock.py`, modulos
`graph_store.py` e `vector_store.py` nas funcoes adicionadas, `pipeline.py` nos caminhos
de skip/reconcile/delete.

---

## Fora de escopo

- **Eliminacao da dupla-parse em `graph_extractor.py`:** o `extract_calls()` re-parseia o
  conteudo dos chunks; unificar com a parse do chunker reduz CPU mas e uma mudanca
  arquitetural maior. Enderecar no Pilar A ou num spec dedicado.
- **Cap de tamanho de chunks (Python/TypeScript):** o `_MAX_CHUNK_LINES` so existe para
  Java (`chunker.py:37`). Adicionar cap para Python/TypeScript e escopo do Pilar A.
- **GPU / `CUDAExecutionProvider`:** escopo do Pilar B (providers onnxruntime). Este pilar
  nao toca o `EmbedderEngine`.
- **Multiprocessing customizado (pool de workers):** YAGNI ate Pilar B medir throughput
  com threading nativo do onnxruntime.
- **Remover `pb index`/`pb index-dev`:** esses comandos continuam funcionando; o
  `file_cache` sera opcional (injetado pelo caller que o quiser).
- **Suporte a novos languages (Rust, Go, Bash):** escopo do chunker, nao deste pilar.
- **Rollback de migration SQLite:** o sistema de migration atual nao tem down-migration;
  adicionar esse mecanismo e escopo separado de infra de DB.
