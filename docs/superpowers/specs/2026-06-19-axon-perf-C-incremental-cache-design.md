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
def _chunk_id(path: Path, chunk: Chunk) -> str:
    import uuid
    key = f"{path}::{chunk.symbol}::{chunk.start_line}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, key))
```

Editar 3 linhas acima de um simbolo desloca `start_line` de todos os chunks abaixo -
todos os IDs mudam - os pontos antigos ficam orfaos no Qdrant. O upsert atual nao deleta
os pontos do arquivo antes de reinserir; apenas faz `upsert` com novos IDs, deixando os
pontos velhos acumulados.

**Decisao D1** resolve isso: o novo `_chunk_id` usa `uuid5(NAMESPACE_URL,
f"{file_path}::{symbol}::{occurrence_index}")` onde `occurrence_index` e o indice
0-based daquele nome de simbolo dentro do arquivo. Isso desambigua overloads e sub-chunks
(ex: `foo[0]`/`foo[1]`). Com IDs estaveis, editar linhas acima de um simbolo nao mais
muda o id daquele simbolo - logo nenhum ponto orfao e criado por deslocamento de linha.

A solucao para os orfaos que ja existem e o delete-by-file descrito em 3d abaixo.

Verificacao empirica necessaria antes de implementar (ver ledger de hipoteses abaixo):
rolar `client.scroll()` para um arquivo editado e confirmar que o count aumenta em vez de
manter-se estavel.

### Redis sequencial nao-pipelinado

O loop de `upsert_deps` em `pipeline.py:196-202`:

```python
# pipeline.py:196-202
if graph_store is not None and graph_chunks:
    for record in build_dependency_records(graph_chunks):
        await graph_store.upsert_deps(
            record.symbol,
            calls=record.calls,
            called_by=record.called_by,
        )
```

Cada `upsert_deps` dispara **um** `hset` Redis (`graph_store.py:34-46`). Para 100 simbolos
com latencia de 1 ms/round-trip = minimo de 100 ms sequenciais. Redis suporta pipelining
nativo; nao esta sendo usado.

### Hipotese de uso de memoria excessivo

A chamada `build_dependency_records(graph_chunks)` ocorre no final de `index_path()`
(`pipeline.py:196`) apos a variavel `graph_chunks: list[Chunk]` (`pipeline.py:141`) ter
acumulado **todos** os chunks de **todos** os arquivos do repo durante o walk. Esta e a
hipotese mais provavel para uso de memoria elevado em repos grandes. A confirmacao ou
refutacao depende do profiling de Phase 0. O streaming de `build_dependency_records` por
arquivo (processar e descartar por arquivo em vez de acumular a lista inteira) e a correcao
proposta, mas pertence ao escopo do **Pilar A** - nao deste spec. Nenhuma afirmacao causal
sobre "14 GB" deve ser tratada como fato antes da medicao.

### Infraestrutura de migracao SQLite ja existente

O `SessionStore` ja usa migrations `.sql` em ordem alfabetica, rastreadas em
`schema_version` (`session_store.py:44-61`). Adicionar `003_file_index.sql` e suficiente
sem mudanca de codigo - o `_apply_migrations()` ja lida com novos arquivos.

Fatos verificados no codigo:
- `session_store.py:44-61` - `_apply_migrations()` le migrations de `store/migrations/`,
  compara com `schema_version`, executa apenas as novas. Usa `executescript()` para cada
  arquivo .sql encontrado via `sorted(_MIGRATIONS_DIR.glob("*.sql"))`.
- `session_store.py:101` - `asyncio.Lock` presente no `SessionStore.__init__`.
- `session_store.py:109-112` - WAL mode + `busy_timeout=5000` + `synchronous=NORMAL` ja
  configurados via PRAGMA na primeira conexao.
- `store/migrations/000_baseline.sql` - tabelas base: `adr`, `session_memory`, etc.
- `store/migrations/001_axon_graph.sql` - tabelas de grafo: `nodes`, `edges`, `sessions`.
- `store/migrations/002_unique_edges.sql` - dedup de edges, index `ux_edges_triple`.
- `pipeline.py:59-75` - `iter_supported_files` usa `rglob('*')` com poda manual de dirs.
- `pipeline.py:161` - hash calculado como `hashlib.sha1(source.encode("utf-8")).hexdigest()`
  (texto UTF-8, NAO bytes brutos).
- `vector_store.py:93-114` - `upsert_batch` agrupa por ctx, um upsert Qdrant por ctx.
- `vector_store.py:163-169` - `delete_by_file(ctx, file_path)` ja existe; recebe ctx e
  file_path, deleta por filtro `FieldCondition(key="file_path")`.
- `graph_store.py:34-46` - `upsert_deps(symbol, calls, called_by)` = um `hset` Redis
  por simbolo.
- `code/indexer.py:71-89` - `_iter_repo_files` ja usa `git ls-files --cached --others
  --exclude-standard`; serve de referencia para a versao a adotar em `pipeline.py`.

---

## Ledger de hipoteses (verificar barato antes de implementar)

| # | Hipotese | Verificacao barata | Onde registrar |
|---|---|---|---|
| H1 | Pontos orfaos ja existem nos 9 repos indexados | `client.scroll()` antes e depois de editar 1 arquivo; checar se count sobe | `benchmarks/phase0_baseline.json` |
| H2 | `_FILE_HASH_CACHE` in-memory e a causa do re-embed total a cada processo | Logar hash-hits vs misses num `axon init` em repo ja indexado; se 0 hits, confirma | log de debug temporario |
| H3 | Redis loop sequential adiciona latencia mensuravel (>100 ms para 100+ simbolos) | `perf_counter()` ao redor do loop em `pipeline.py:196-202` num repo de 200+ simbolos | `benchmarks/phase0_profile.json` |
| H4 | O `rglob` sem poda nao e o gargalo principal de wall time (embedding domina) | `time` em `iter_supported_files()` isolado vs wall time total de `index_path()` | `benchmarks/phase0_profile.json` |
| H5 | Colisoes de `uuid5` nao ocorrem hoje nos 9 repos | Script de varredura de IDs duplicados; resultado esperado: dict vazio | verificacao unica pre-deploy |
| H6 | Acumulacao de `graph_chunks` e a causa principal do pico de RAM em repos grandes | Profilear RSS com `psutil` ao redor de `pipeline.py:141-196`; comparar antes/apos streamer | `benchmarks/phase0_baseline.json` |
| H7 | `os.kill(pid, 0)` para reclaim de lock stale funciona corretamente no Windows 11 | `test_index_lock_windows.py`: criar lockfile com PID de processo terminado; executar `acquire_index_lock`; verificar que o reclaim ocorre sem erro | `benchmarks/phase0_baseline.json` |

**Nenhuma hipotese pode ser declarada como fato no plano de implementacao ate ser medida.**
O gate de Phase 0 (definido abaixo) e o controle.

---

## Decisoes

| Tema | Decisao | Racional |
|---|---|---|
| Chunk-id estavel (D1) | `uuid5(NAMESPACE_URL, f"{file_path}::{symbol}::{occurrence_index}")` onde `occurrence_index` e o indice 0-based daquele nome no arquivo | Elimina re-indexacao forcada por deslocamento de linha; desambigua overloads e sub-chunks sem depender de `start_line` |
| Armazenamento do cache de hash | Tabela `file_index` no SQLite existente (mesmo DB do `SessionStore`) | Reutiliza infra, migrations, locking e WAL ja configurados; zero nova dependencia |
| Schema da tabela | `file_path TEXT, ctx TEXT, sha1 TEXT, status TEXT, chunk_count INTEGER, indexed_at TEXT, PRIMARY KEY (file_path, ctx)` | Coluna `status` habilita o sentinel de crash-safety (D2) |
| Versao de schema | Migration `003_file_index.sql` na pasta `store/migrations/` | O `_apply_migrations()` existente executa sem mudanca de codigo |
| Crash-safety / sentinel (D2) | Escrever `status='pending'` + novo sha ANTES de mutar o Qdrant; setar `status='done'` apos upsert bem-sucedido; ao iniciar, tratar qualquer linha `pending` como dirty e re-indexar | Elimina a janela de dados perdidos: crash entre delete e upsert resulta em re-index na proxima execucao, nunca em cache stale + Qdrant vazio |
| Migracao one-shot (D2 blue/green) | Para os 9 repos ja indexados: indexar em nova colecao Qdrant, rodar recall gate, promover via alias swap somente se aprovado | Normal incremental nao usa blue/green; apenas a migracao one-shot usa |
| Reconcile Qdrant | Delete-all-for-file + re-add, usando `vector_store.delete_by_file` existente (D4) | Com D1 (ids estaveis), arquivos nao-modificados mantem pontos validos mesmo com deslocamento de linha; arquivos modificados (hash miss) passam por delete+upsert, eliminando simbolos deletados/renomeados |
| Orfaos resolvidos por D1 + D6 | Arquivos sem hash-change mantem pontos validos (D1 garante ids estaveis); arquivos com hash-change fazem delete_by_file + re-upsert (limpa simbolos removidos/renomeados) | O problema de orfaos e resolvido pela combinacao de D1 (sem mudanca de id por linha shift) e delete-by-file no hash-miss (limpa simbolos extintos); nao ha necessidade de diff por chunk-id |
| Metodo de delete | Reutilizar `vector_store.delete_by_file(ctx, file_path)` ja em `vector_store.py:163`; chamar em loop sobre COLLECTIONS / VALID_CONTEXTS para delete all-context quando necessario (D4) | Nao adicionar nenhum novo metodo de delete; `delete_by_file` ja existe e cobre o caso |
| Walk de arquivos (D3) | Substituir `rglob` em `iter_supported_files` por `git ls-files --cached` (NAO --others) como fonte primaria; filtrar cada path por `git check-ignore` para excluir arquivos que foram commitados e depois adicionados ao .gitignore; fallback para `rglob` se nao for repo git | Garantia de seguranca: nenhum arquivo gitignored e jamais embeddado; arquivos nao-rastreados requerem `git add` antes de serem indexados |
| Hash de arquivo | Manter `hashlib.sha1(source.encode("utf-8")).hexdigest()` - identico ao codigo atual em `pipeline.py:161` | Evita full re-embed no primeiro deploy; qualquer mudanca de metodo de hash causaria cold-start completo e deve ser documentada explicitamente como one-time cost |
| Redis pipelining | Batch de N `hset` num unico pipeline por `upsert_deps_batch` | `redis-py` suporta `pipe()` nativo; mudanca de 3 linhas, ganho proporcional a N simbolos |
| Atomicidade Redis | `pipeline(transaction=False)` - sem MULTI/EXEC overhead | Falha parcial e corrigida na proxima execucao (reconcile re-indexara o arquivo); adicionar um teste que verifica ausencia de dados corrompidos apos falha simulada |
| `FileCache` obrigatoria | `FileCache` e parametro obrigatorio de `index_path`; remover guards `if file_cache:` | YAGNI - o bypass opcional e especulativo; todos os callers devem passar uma instancia real ou mock |
| Locking de concorrencia | Lockfile `.axon/index.lock` com PID escrito no arquivo; verificar se PID existe via `os.kill(pid, 0)` antes de reclamar lock stale; adicionar TTL como fallback adicional. HIPOTESE em Windows 11: `os.kill(pid, 0)` pode nao ter o mesmo comportamento que em Unix - verificar em Phase 0 com teste Windows-especifico antes de confiar no reclaim automatico em producao | `O_EXCL` sozinho bloqueia indexacoes futuras apos crash; escrever PID permite reclaim automatico de locks abandonados |
| Score de recall cross-plataforma | Calibrar `min_score` separadamente para bge-base (768-dim) e bge-small (384-dim); armazenar em `score_calibration.json` | Um threshold fixo de 0.70 sem calibracao nao e confiavel entre modelos com dimensoes diferentes |
| `executescript` SQLite | Comportar como propriedade conhecida: `executescript` emite COMMIT implicito antes de executar; `003_file_index.sql` e DDL puro com `IF NOT EXISTS` logo re-execucoes sao seguras | Nao e um bug; e documentado como comportamento do modulo sqlite3 do Python |
| Migracao scroll paginado | Paginar `client.scroll()` via `next_page_offset` ate `None` (ou usar `count()` para verificar zero orfaos) em vez de `limit=10000` | Colecoes com >10000 pontos retornariam resultado truncado silenciosamente |
| Cache de sha1 em batch | Adicionar `get_all_sha1s(ctx)` ao `SqliteFileCache`: um unico `SELECT` retorna todos os (file_path, sha1) do ctx; comparacao feita em memoria | Evita N roundtrips asyncio no tight loop de `index_path`; reducao de contencao no Lock |
| Normalizacao de path | Normalizar todos os `file_path` armazenados para `Path(p).as_posix()` antes de escrever no `file_index` e antes de usar como filtro Qdrant | Git emite `/` em todos os OS; `Path` no Windows emite `\\`; inconsistencia causaria misses no lookup e orfaos nao-detectados |

---

## Componentes e mudancas

### 1. Migration `003_file_index.sql`

Arquivo novo em `C:/Users/samde/dev/axon/src/axon/store/migrations/003_file_index.sql`:

```sql
-- 003_file_index.sql
-- Cache persistente de hashes por arquivo para skip incremental cross-process.
-- Requer: 000_baseline, 001_axon_graph, 002_unique_edges ja aplicados.
-- executescript() emite COMMIT implicito antes de executar; DDL puro com
-- IF NOT EXISTS torna re-execucao segura.

CREATE TABLE IF NOT EXISTS file_index (
    file_path   TEXT    NOT NULL,
    ctx         TEXT    NOT NULL,
    sha1        TEXT    NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'done',  -- 'pending' | 'done'
    chunk_count INTEGER NOT NULL DEFAULT 0,
    indexed_at  TEXT    NOT NULL,  -- ISO-8601 UTC
    PRIMARY KEY (file_path, ctx)
);

CREATE INDEX IF NOT EXISTS ix_file_index_ctx
    ON file_index (ctx);

CREATE INDEX IF NOT EXISTS ix_file_index_status
    ON file_index (status);
```

Notas de design:
- PK composta `(file_path, ctx)` porque o mesmo arquivo pode ser indexado em contextos
  diferentes (ex: `knowledge` e `work`).
- Coluna `status` implementa o sentinel de crash-safety (D2): `'pending'` indica que a
  mutacao Qdrant esta em andamento ou foi interrompida; `'done'` indica estado consistente.
- `chunk_count` permite validar se o numero de chunks mudou sem precisar ler o Qdrant.
- `CREATE TABLE IF NOT EXISTS` garante idempotencia (re-aplicacao segura).
- O `_apply_migrations()` em `session_store.py:44-61` detecta `003_file_index.sql` e
  executa na proxima inicializacao do `SessionStore` - nenhuma mudanca de codigo necessaria.

### 2. Modulo `axon/store/file_cache.py` (novo)

Responsabilidade unica: ler e escrever `file_index`. Isola toda a logica de cache do
`pipeline.py`. `FileCache` e um Protocol; `SqliteFileCache` e a implementacao concreta.

```python
# axon/store/file_cache.py
from __future__ import annotations
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol


class FileCache(Protocol):
    async def get_all_sha1s(self, ctx: str) -> dict[str, str]: ...
    # Retorna {file_path_posix: sha1} para o ctx dado (um unico SELECT)

    async def set_entry(
        self, file_path: str, ctx: str, sha1: str, chunk_count: int, *,
        status: str = "done",
    ) -> None: ...

    async def delete_entry(self, file_path: str, ctx: str) -> None: ...

    async def list_entries(self, ctx: str) -> list[tuple[str, str]]: ...
    # Retorna lista de (file_path_posix, sha1) para o ctx dado


class SqliteFileCache:
    """Implementacao concreta usando a conexao aiosqlite do SessionStore."""

    def __init__(self, conn, lock):  # aiosqlite.Connection, asyncio.Lock
        self._conn = conn
        self._lock = lock

    async def get_all_sha1s(self, ctx: str) -> dict[str, str]:
        """Retorna {file_path: sha1} em um unico SELECT; comparacao feita em memoria."""
        async with self._lock:
            cur = await self._conn.execute(
                "SELECT file_path, sha1 FROM file_index WHERE ctx=? AND status='done'",
                (ctx,),
            )
            rows = await cur.fetchall()
        return {row[0]: row[1] for row in rows}

    async def set_entry(
        self,
        file_path: str,
        ctx: str,
        sha1: str,
        chunk_count: int,
        *,
        status: str = "done",
    ) -> None:
        # Normalizar para posix antes de armazenar
        fp = Path(file_path).as_posix()
        now = datetime.now(timezone.utc).isoformat()
        async with self._lock:
            await self._conn.execute(
                """
                INSERT INTO file_index
                    (file_path, ctx, sha1, status, chunk_count, indexed_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (file_path, ctx) DO UPDATE SET
                    sha1        = excluded.sha1,
                    status      = excluded.status,
                    chunk_count = excluded.chunk_count,
                    indexed_at  = excluded.indexed_at
                """,
                (fp, ctx, sha1, status, chunk_count, now),
            )
            await self._conn.commit()

    async def delete_entry(self, file_path: str, ctx: str) -> None:
        fp = Path(file_path).as_posix()
        async with self._lock:
            await self._conn.execute(
                "DELETE FROM file_index WHERE file_path=? AND ctx=?",
                (fp, ctx),
            )
            await self._conn.commit()

    async def list_entries(self, ctx: str) -> list[tuple[str, str]]:
        async with self._lock:
            cur = await self._conn.execute(
                "SELECT file_path, sha1 FROM file_index WHERE ctx=?", (ctx,)
            )
            return await cur.fetchall()


def sha1_of_source(source: str) -> str:
    """Hash do conteudo UTF-8 do arquivo - identico ao pipeline.py:161 atual.

    NAO usar path.read_bytes() - isso produziria digest diferente e causaria
    full re-embed no primeiro deploy. Se for necessario migrar para read_bytes(),
    documentar como one-time cold-start explicito.

    Sem kwarg usedforsecurity: identico ao pipeline.py:161 que tambem nao usa o kwarg.
    Em sistemas FIPS o Python pode rejeitar hashlib.sha1() sem usedforsecurity=False;
    se isso for necessario, pipeline.py:161 deve ser atualizado no mesmo PR para manter
    os digests identicos entre as duas chamadas.
    """
    return hashlib.sha1(source.encode("utf-8")).hexdigest()
```

Dependencias: `aiosqlite` (ja em uso), `asyncio.Lock` (ja no `SessionStore`).
Sem nova dependencia de terceiros.

### 3. Alteracoes em `pipeline.py`

#### 3a. Substituir `_FILE_HASH_CACHE` por `FileCache` (obrigatoria)

Remover `pipeline.py:28`:
```python
# REMOVER:
_FILE_HASH_CACHE: dict[str, str] = {}
```

Adicionar `file_cache: FileCache` como parametro **obrigatorio** de `index_path`.
Nao ha fallback `None` - todos os callers devem passar uma instancia real ou mock.
O comportamento anterior (sem skip) era resultado de sempre ter um dict vazio; os testes
que precisam desse comportamento devem passar um mock que sempre retorna `None` para
`get_all_sha1s`.

#### 3b. Novo `_chunk_id` estavel (D1)

Substituir a funcao atual (`pipeline.py:206-211`) por:

```python
# pipeline.py - substitui a funcao _chunk_id
def _chunk_id(file_path: Path, chunk: Chunk, occurrence_index: int) -> str:
    """ID estavel: nao depende de start_line.

    occurrence_index = indice 0-based daquele nome de simbolo dentro do arquivo
    (ex: segundo metodo com nome 'process' tem occurrence_index=1).
    Desambigua overloads e sub-chunks (foo[0]/foo[1]) sem usar numero de linha.
    """
    import uuid
    key = f"{Path(file_path).as_posix()}::{chunk.symbol}::{occurrence_index}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, key))
```

O caller deve rastrear um `Counter[str]` de nomes de simbolo por arquivo e passar
`counter[chunk.symbol]` como `occurrence_index` antes de incrementar.

#### 3c. Logica de skip incremental com sentinel de crash-safety (D2)

```python
# Pseudocodigo - index_path() apos receber file_cache: FileCache

# --- Pre-carregamento de todos os sha1s em um unico SELECT ---
cached_sha1s: dict[str, str] = await file_cache.get_all_sha1s(ctx)
# cached_sha1s contem apenas linhas com status='done'

# Qualquer linha 'pending' sobrevivente de crash sera invisivel aqui ->
# ausente em cached_sha1s -> tratada como hash miss -> re-indexada

# --- Loop por arquivo ---
for file_path in files:
    fp_posix = Path(file_path).as_posix()
    source = file_path.read_text(encoding="utf-8", errors="replace")
    current_sha1 = sha1_of_source(source)

    if cached_sha1s.get(fp_posix) == current_sha1:
        stats["skipped"] += 1
        continue  # arquivo nao mudou - pular

    # (1) Escrever sentinel ANTES de mutar Qdrant
    await file_cache.set_entry(fp_posix, ctx, current_sha1, 0, status="pending")

    # (2) Deletar pontos antigos; acumular chunks no batch deferred
    await store.delete_by_file(ctx, fp_posix)
    chunks = chunk_source(source, language, str(file_path))
    pending_batch.extend(chunks)
    pending_file_meta.append((fp_posix, current_sha1, len(chunks)))
    # IMPORTANTE: set_entry(done) NAO ocorre aqui dentro do loop.
    # Chunks ainda nao foram upsertados ao Qdrant (estao no pending_batch).
    # Marcar done antes do flush causaria: crash => cache='done' mas Qdrant vazio.

    if len(pending_batch) >= _BATCH_SIZE:
        # (3a) Flush do batch ANTES de marcar done
        await _flush_batch(pending_batch, engine, store, ctx)
        pending_batch.clear()
        # (3b) Somente apos flush bem-sucedido, marcar todos os arquivos do batch como done
        for fp, s1, cc in pending_file_meta:
            await file_cache.set_entry(fp, ctx, s1, cc, status="done")
        pending_file_meta.clear()

# Apos o loop: flush do ultimo batch parcial, depois marcar done
await _flush_batch(pending_batch, engine, store, ctx)
for fp, s1, cc in pending_file_meta:
    await file_cache.set_entry(fp, ctx, s1, cc, status="done")
```

Se o processo travar entre (1) e (3b), a linha permanece `status='pending'`. Na proxima
execucao, `get_all_sha1s` filtra apenas `status='done'`, logo o arquivo e tratado como
miss e re-indexado completamente. A invariante e: `status='done'` implica que o batch
contendo os chunks daquele arquivo ja foi persistido no Qdrant.

#### 3d. Substituir `git ls-files` por `rglob` em `iter_supported_files` (D3)

Alterar `pipeline.py:59-75` para usar `git ls-files --cached` (NAO --others) como
fonte primaria, com filtro de `git check-ignore` para arquivos que foram commitados e
depois adicionados ao .gitignore:

```python
# Pseudocodigo - iter_supported_files (D3)
import subprocess

def iter_supported_files(target: Path, *, languages: set[str] | None = None):
    if target.is_file():
        language = _language_for_suffix(target.suffix)
        if language and (languages is None or language in languages):
            yield target
        return

    try:
        result = subprocess.run(
            ["git", "-C", str(target), "ls-files", "--cached"],
            capture_output=True, text=True, check=True,
        )
        for line in result.stdout.splitlines():
            p = target / line.strip()
            # Normalizar para comparacoes consistentes
            if p.suffix not in _LANGUAGE_MAP:
                continue
            if not p.is_file():
                continue
            # Excluir arquivos que foram commitados e depois gitignored
            chk = subprocess.run(
                ["git", "-C", str(target), "check-ignore", "-q", str(p)],
                capture_output=True,
            )
            if chk.returncode == 0:
                continue  # gitignored - nao embeddar
            language = _language_for_suffix(p.suffix)
            if language and (languages is None or language in languages):
                yield p
    except (subprocess.CalledProcessError, FileNotFoundError):
        # Fallback: rglob com poda manual (repo nao-git ou git nao disponivel)
        for p in target.rglob("*"):
            if any(part in EXCLUDED_DIR_NAMES for part in p.parts):
                continue
            language = _language_for_suffix(p.suffix)
            if p.is_file() and language and (languages is None or language in languages):
                yield p
```

Garantia de seguranca (D3): `git ls-files --cached` lista apenas arquivos rastreados.
Arquivos nao-rastreados (nao submetidos a `git add`) nao aparecem. O filtro
`git check-ignore` exclui arquivos que foram commitados e depois adicionados ao .gitignore.
Resultado: **nenhum arquivo gitignored e jamais embeddado**. Adicionar um teste de
seguranca obrigatorio (ver secao de testes).

Todos os `file_path` armazenados devem ser normalizados via `Path(p).as_posix()` antes
de qualquer escrita em `file_index` ou uso como filtro Qdrant - evita mismatch entre
paths emitidos pelo git (sempre `/`) e `Path` no Windows (emite `\\`).

#### 3e. Reconcile Qdrant por arquivo (delete-then-upsert) (D4 + D6)

Antes de chunk/embed de um arquivo modificado (hash miss), deletar todos os pontos do
arquivo naquele ctx usando o metodo **ja existente** `vector_store.delete_by_file`:

```python
# Pseudocodigo - apos detectar hash miss, antes de chunk/embed
# NAO criar novo metodo; usar delete_by_file que ja existe em vector_store.py:163
await store.delete_by_file(ctx, fp_posix)
```

Para delete all-context (ex: arquivo deletado do repo), chamar em loop:

```python
# Pseudocodigo - arquivo removido do repo
from axon.context.registry import VALID_CONTEXTS
for ctx_name in VALID_CONTEXTS:
    await store.delete_by_file(ctx_name, fp_posix)
```

Nao adicionar `delete_file_points`, `delete_by_file_path`, `_collections()` ou qualquer
outro metodo de delete. O `delete_by_file` existente e suficiente (D4).

**Como D1 + D6 resolvem o problema de orfaos:**
- Arquivo **nao modificado** (hash hit): ids sao estaveis (D1 garante que deslocamento de
  linha nao muda o id), logo os pontos existentes continuam validos. Nenhum re-index.
- Arquivo **modificado** (hash miss): `delete_by_file` remove todos os pontos do arquivo
  antes do upsert. Simbolos deletados ou renomeados nao sao mais inseridos - orfaos
  eliminados automaticamente.

#### 3f. Deteccao de arquivos deletados

Apos o walk de arquivos, comparar a lista de arquivos encontrados com `list_entries(ctx)`
do cache, **escopado ao mesmo ctx**. Arquivos no cache para aquele ctx mas ausentes no
walk foram deletados:

```python
# Pseudocodigo - ao final de index_path(), antes de retornar
# found_paths contem apenas paths do ctx atual (nao misturar ctxs)
found_paths = {Path(p).as_posix() for p in iterated_files_for_this_ctx}
cached_entries = await file_cache.list_entries(ctx)  # escopado ao mesmo ctx
for cached_path, _ in cached_entries:
    if cached_path not in found_paths:
        await store.delete_by_file(ctx, cached_path)
        await file_cache.delete_entry(cached_path, ctx)
        stats["deleted"] += 1
```

A comparacao usa apenas entradas do mesmo ctx, evitando falsos positivos onde um arquivo
existe em `knowledge` mas nao em `work`.

### 4. Redis pipelining em `pipeline.py` e `graph_store.py`

#### 4a. Assinatura atual de `upsert_deps` (`graph_store.py:34-46`)

```python
# graph_store.py:34-46 - ATUAL
async def upsert_deps(
    self,
    symbol: str,
    calls: list[str],
    called_by: list[str],
) -> None:
    await self._redis.hset(
        f"dep:{symbol}",
        mapping={
            "calls": json.dumps(calls),
            "called_by": json.dumps(called_by),
        },
    )
```

#### 4b. Novo metodo `upsert_deps_batch` em `graph_store.py`

```python
# graph_store.py - novo metodo
async def upsert_deps_batch(
    self,
    records: list[DependencyRecord],  # DependencyRecord tem .symbol, .calls, .called_by
) -> None:
    if not records:
        return
    async with self._redis.pipeline(transaction=False) as pipe:
        for record in records:
            pipe.hset(
                f"dep:{record.symbol}",
                mapping={
                    "calls": json.dumps(record.calls),
                    "called_by": json.dumps(record.called_by),
                },
            )
        await pipe.execute()
```

`transaction=False` evita o overhead do `MULTI/EXEC` para upserts sem necessidade de
atomicidade entre simbolos distintos. Falha parcial e corrigida na proxima execucao de
`index_path` (o arquivo sera re-indexado por hash miss ou por nova edicao). Adicionar um
teste que simula falha no meio do pipeline e verifica ausencia de dados corrompidos.

#### 4c. Chamada no `pipeline.py`

```python
# pipeline.py - substituir loop sequencial por batch
await graph_store.upsert_deps_batch(dep_records)
```

Verificacao de ganho: medir `perf_counter()` ao redor do loop atual num repo de 200+
simbolos antes de deployar (hipotese H3 no ledger). Se o ganho for < 20 ms, o pipeline
continua valendo pela reducao de roundtrips mas nao e urgente.

### 5. Locking e concorrencia

#### Cenario de risco

O git hook (`python -m axon.hooks.git_event post-commit`) e um `axon index` manual podem
ser disparados em paralelo - processos separados, mesmo repo.

#### Camadas de protecao

| Camada | Mecanismo | Cobre |
|---|---|---|
| SQLite WAL | `journal_mode=WAL` + `busy_timeout=5000` (`session_store.py:109-112`) | Dois processos lendo/escrevendo `file_index` simultaneamente |
| `asyncio.Lock` | Lock existente no `SessionStore.__init__` (linha 101), repassado ao `SqliteFileCache` | Coroutines concorrentes no mesmo processo |
| Qdrant | Qdrant aceita upserts e deletes concorrentes sem corrupcao de dados | Duplicate-upsert possivel; resolvido pelo reconcile por arquivo |
| Arquivo `.axon/index.lock` | Lockfile com PID; verificacao `os.kill(pid, 0)` para reclaim de lock stale | Impede dois processos de indexar o mesmo repo simultaneamente; lock abandonado por crash e reclamado automaticamente |

O lockfile com PID resolve o problema de lock stale:

```python
# axon/store/index_lock.py - novo modulo
import os
from contextlib import asynccontextmanager
from pathlib import Path


class IndexLockError(Exception):
    pass


def _pid_alive(pid: int) -> bool:
    """Retorna True se o processo com o pid dado ainda esta em execucao.

    HIPOTESE (verificar em Phase 0 no Windows 11):
    - Em Unix/macOS: os.kill(pid, 0) levanta ProcessLookupError se o PID nao existe,
      PermissionError se existe mas pertence a outro usuario - comportamento confiavel.
    - Em Windows 11 (R7 5800X3D): os.kill() e implementado via TerminateProcess() com
      signal=0 nao tendo efeito padronizado; o comportamento pode diferir.
      Adicionar um teste de integracao Windows-especifico (test_index_lock_windows.py)
      que cria um lockfile com PID de processo ja terminado e verifica que o reclaim
      automatico funciona corretamente antes de confiar nesta logica em producao.
    """
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


@asynccontextmanager
async def acquire_index_lock(repo_root: Path):
    lock_path = repo_root / ".axon" / "index.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    if lock_path.exists():
        try:
            existing_pid = int(lock_path.read_text().strip())
            if _pid_alive(existing_pid):
                raise IndexLockError(
                    f"Outro processo (pid={existing_pid}) esta indexando {repo_root}. "
                    f"Se o processo anterior travou, remova: {lock_path}"
                )
            # PID nao existe mais - lock abandonado por crash, reclamar
            lock_path.unlink(missing_ok=True)
        except ValueError:
            # Arquivo de lock com conteudo invalido - reclamar
            lock_path.unlink(missing_ok=True)

    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
    except FileExistsError:
        raise IndexLockError(
            f"Race condition ao adquirir lock em {lock_path}. Tentar novamente."
        )
    try:
        yield
    finally:
        lock_path.unlink(missing_ok=True)
```

O hook post-commit nunca deve bloquear o git. Se o lockfile existir e o PID estiver
ativo, o hook loga um aviso e sai com exit 0 (comportamento identico ao pattern em
`git_installer.py` onde falhas sao swallowed via `|| true`). A indexacao sera feita
no proximo commit ou via `axon index` manual.

Adicionar um teste que verifica reclaim automatico de lock stale (pid inexistente).
Adicionar `test_index_lock_windows.py` especificamente para o R7 5800X3D (Windows 11):
verificar que `_pid_alive` retorna False para um PID de processo terminado e que o
reclaim prossegue corretamente. Este teste e obrigatorio antes de declarar o reclaim
como funcionalidade suportada em Windows - ate ser validado, tratar como hipotese H7
(ver ledger de hipoteses).

### 6. Migracao one-shot dos 9 repos ja indexados (D2 blue/green)

#### Contexto

Os 9 repos foram indexados com a logica antiga (sem `file_index`, possivelmente com ctx
`personal` ou outros ctx legados). Apos deployar este pilar, o cache SQLite estara vazio
para todos eles.

O problema e que pontos orfaos ja existem no Qdrant (hipotese H1). O reindex sem purge
apenas adiciona novos pontos por cima dos velhos.

#### Procedimento blue/green (somente para migracao one-shot)

A migracao one-shot usa blue/green para garantir rollback sem downtime. Execucoes
incrementais normais **nao** usam blue/green.

```bash
# Passo 1 - listar colecoes existentes e confirmar nomes de ctx legados
python - <<'EOF'
from qdrant_client import QdrantClient
client = QdrantClient("http://localhost:6333")
for col in client.get_collections().collections:
    print(col.name)
EOF

# Passo 2 - criar colecoes novas com sufixo _new (blue/green)
# (substituir "knowledge" pelo(s) ctx real(is) confirmado(s) no passo 1)
python - <<'EOF'
from qdrant_client import QdrantClient
from qdrant_client.models import VectorParams, Distance
client = QdrantClient("http://localhost:6333")
# Exemplo para ctx "knowledge" - repetir para cada ctx a migrar
client.create_collection(
    collection_name="knowledge_new",
    vectors_config=VectorParams(size=768, distance=Distance.COSINE),
)
EOF

# Passo 3 - reindex completo dos 9 repos apontando para colecoes _new
# (requer parametro de target_collection no indexer ou renomeio temporario)
axon index <vault_root> --ctx knowledge --target-collection knowledge_new

# Passo 4 - rodar recall gate contra knowledge_new
# (preencher os 20 queries do golden set contra a nova colecao)
# Gate: Top-1 >= 0.90, Top-3 >= 0.95, score >= 0.90
# Se falhar, manter colecao antiga e investigar antes de prosseguir

# Passo 5 - promover via alias swap SOMENTE se recall gate aprovado
python - <<'EOF'
from qdrant_client import QdrantClient
client = QdrantClient("http://localhost:6333")
# Alias swap atomico
client.update_collection_aliases(change_aliases_operations=[
    {"delete_alias": {"alias_name": "knowledge"}},
    {"create_alias": {"collection_name": "knowledge_new", "alias_name": "knowledge"}},
])
EOF

# Passo 6 - verificar ausencia de orfaos pos-migracao via scroll paginado
python - <<'EOF'
from qdrant_client import QdrantClient
client = QdrantClient("http://localhost:6333")
all_points = []
offset = None
while True:
    result, next_offset = client.scroll(
        "knowledge", limit=1000, with_payload=True, offset=offset
    )
    all_points.extend(result)
    if next_offset is None:
        break
    offset = next_offset
paths = {p.payload.get("file_path") for p in all_points}
print(f"Total pontos: {len(all_points)}")
print(f"Paths distintos: {len(paths)}")
# Inspecionar manualmente se algum path e inesperado
EOF
```

O reindex completo e necessario uma unica vez. Apos isso, o `file_index` tem estado
correto e todos os refreshes subsequentes serao incrementais.

### 7. Calibracao de scores cross-plataforma

O threshold `min_score` no golden set nao pode ser um valor fixo sem calibracao entre
modelos com dimensoes diferentes (bge-base 768-dim no R7 desktop vs bge-small 384-dim
no M1 Pro). A calibracao deve ser feita uma vez em cada maquina e armazenada em
`tests/recall/score_calibration.json`:

```json
{
  "bge-base-en-v1.5": { "min_score": 0.XX, "calibrated_at": "2026-..." },
  "bge-small-en-v1.5": { "min_score": 0.XX, "calibrated_at": "2026-..." }
}
```

O harness de recall le `score_calibration.json` e usa o threshold correto para o modelo
ativo. Os valores XX devem ser determinados experimentalmente na Phase 0, nao assumidos.

---

## Fluxo de dados (apos este pilar)

```
axon index <repo> --ctx knowledge
    |
    +-- acquire_index_lock(repo_root)   # impede concorrencia multi-processo
    |                                   # reclaim automatico de lock stale (PID)
    |
    +-- cached_sha1s = await file_cache.get_all_sha1s(ctx)  # um SELECT
    |   # rows com status='pending' sao filtradas -> tratadas como hash miss
    |
    +-- iter_supported_files(repo)      # git ls-files --cached + git check-ignore
    |   pending_file_meta = []  # acumula (fp_posix, sha1, chunk_count) ate apos flush
    |   para cada arquivo (normalizar path para posix):
    |     current_sha1 = sha1_of_source(source)
    |     if cached_sha1s.get(fp_posix) == current_sha1:  SKIP
    |     else:
    |       # (1) sentinel ANTES de mutar Qdrant
    |       await file_cache.set_entry(fp_posix, ctx, sha1, 0, status="pending")
    |       # (2) delete; chunks entram no batch deferred (NAO upsertados ainda)
    |       await store.delete_by_file(ctx, fp_posix)  # metodo existente
    |       chunks = chunk_source(source, language, str(file_path))
    |       pending_batch.extend(chunks)
    |       pending_file_meta.append((fp_posix, sha1, len(chunks)))
    |       if len(pending_batch) >= _BATCH_SIZE:
    |         # (3) flush ANTES de marcar done - garante chunks persistidos no Qdrant
    |         await _flush_batch(pending_batch, engine, store, ctx)
    |         pending_batch.clear()
    |         for fp, s1, cc in pending_file_meta:
    |           await file_cache.set_entry(fp, ctx, s1, cc, status="done")
    |         pending_file_meta.clear()
    |         # Invariante: status='done' => chunks ja persistidos no Qdrant
    |
    +-- _flush_batch (ultimo batch restante)
    +-- para cada (fp, s1, cc) em pending_file_meta:
    |     await file_cache.set_entry(fp, ctx, s1, cc, status="done")
    |   # set_entry(done) so ocorre APOS o flush que contem os chunks do arquivo
    |
    +-- build_dependency_records(graph_chunks)   # 2a parse - streaming por arquivo
    |   e escopo do Pilar A; aqui ainda e acumulado para manter compatibilidade
    +-- await graph_store.upsert_deps_batch(dep_records)  # pipelinado
    |
    +-- deteccao de arquivos deletados (list_entries vs found_paths, escopado ao ctx)
    |   para cada deletado: delete_by_file(ctx, path) + delete_entry(path, ctx)
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
| H1 verificada | Confirmar se orfaos existem hoje | `scroll()` paginado antes/depois de editar 1 arquivo |
| H3 verificada | Medir latencia do loop Redis em 200+ simbolos | `perf_counter()` ao redor de `pipeline.py:196-202` |
| H4 verificada | Medir wall time do `rglob` isolado vs total | `time iter_supported_files()` isolado |
| H6 verificada | Medir RSS antes/depois de `build_dependency_records` vs accumulator | `psutil.Process().memory_info().rss` no breakpoint |
| GPU disponivel (Pilar B) | `bool` em `phase0_baseline.json` | `ort.get_available_providers()` |
| Recall baseline >= 0.80 | Top-1 e Top-3 no golden set de 20 queries | Harness de recall (ver abaixo) |
| Score calibrado por modelo | `score_calibration.json` preenchido para ambos os modelos | Medicao experimental em R7 e M1 Pro |

Se o peak RSS exceder 8 GB durante a medicao baseline, a evidencia deve ser registrada
e comunicada ao Pilar A (que e dono do streaming de `build_dependency_records`). Este
pilar nao implementa o streaming - apenas reporta os dados de Phase 0.

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
  "expected_file": "caminho/posix/normalizado.py",
  "expected_symbol": "nome_da_funcao_ou_classe",
  "min_score": "<ver score_calibration.json para o modelo ativo>"
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

As mesmas queries e expected_files devem passar em R7 5800X3D e M1 Pro. O threshold
`min_score` e lido de `score_calibration.json` para o modelo ativo em cada maquina
(bge-base 768-dim no desktop, bge-small 384-dim no mac). Se um par query/expected_file
falhar no mac, o golden set precisa ser revisado antes de deployar.

---

## Criterios de sucesso mensuraveis (por maquina)

| Metrica | R7 5800X3D | M1 Pro | Como medir |
|---|---|---|---|
| Wall time index completo (9 repos, cache frio) | <= 5 min | <= 8 min | `time axon index <vault>` com cache vazio; mediana de 3 runs |
| Wall time refresh incremental (1 arquivo, 10-50 chunks) | <= 10 s | <= 15 s | 5 arquivos de tamanhos variados (10/20/30/40/50 chunks); todos devem passar |
| Wall time hook post-commit (20 arquivos alterados) | <= 30 s | <= 45 s | `python -m axon.hooks.git_event post-commit` cronometrado; maximo de 3 runs |
| Peak RSS index completo (9 repos) | <= 2 GB | <= 1.5 GB | `psutil.Process().memory_info().rss` amostrado a cada 2 s |
| Throughput embedding (chunks/s end-to-end) | >= 300 chunks/s | >= 200 chunks/s | corpus sintetico fixo de 500 funcoes Python (15-30 linhas cada) |
| Recall Top-1 (golden set 20 queries) | >= 0.90 | >= 0.90 | harness em Qdrant real com corpus de referencia (`src/axon/embedder/`, `src/axon/store/`) |
| Recall Top-3 (golden set 20 queries) | >= 0.95 | >= 0.95 | mesmo harness |
| Exclusao de arquivos gitignored | 0 pontos cujo file_path bate .gitignore | 0 pontos | scroll Qdrant pos-index em repo com `.env` e `secrets.json` gitignored |
| Orphan-free pos-reconcile | 0 pontos orfaos apos editar 3 linhas acima de simbolo | 0 pontos orfaos | scroll por file_path antes e depois; count deve ser igual (nao acumular) |
| Seguranca de concorrencia | 0 corrupcoes em 20 trials de index+hook simultaneos | 0 corrupcoes | 2 processos em paralelo via subprocess; scroll pos-execucao; sem IDs duplicados nem JSON invalido no Redis |
| Reclaim de lock stale | Lock abandonado por crash reclamado automaticamente | idem | teste de integracao: criar lock com PID falso, executar index_path, confirmar sucesso |

---

## Unidades (isolamento e testabilidade)

| Modulo | Responsabilidade | Dependencias injetaveis |
|---|---|---|
| `axon/store/file_cache.py::SqliteFileCache` | CRUD na `file_index`; calculo de sha1 | `aiosqlite.Connection`, `asyncio.Lock` |
| `axon/store/index_lock.py::acquire_index_lock` | Lockfile com PID; reclaim de stale | `Path` (repo root) |
| `axon/store/graph_store.py::upsert_deps_batch` | Batch pipeline Redis | `redis.asyncio.Redis` |
| `axon/embedder/vector_store.py::delete_by_file` | Delete Qdrant por (ctx, file_path) - JA EXISTE | `AsyncQdrantClient` |
| `axon/embedder/pipeline.py::index_path` (modificado) | Orquestra skip, reconcile, flush, delete-orfaos | `FileCache`, `VectorStore`, `GraphStore`, `EmbedderEngine` |
| `axon/store/migrations/003_file_index.sql` | Schema da tabela com coluna status | n/a - SQL puro |

Cada unidade e testavel com mocks injetados:
- `SqliteFileCache`: testar `get_all_sha1s` miss/hit, `set_entry` UPSERT com status,
  `delete_entry`, `list_entries` filtrando por ctx.
- `acquire_index_lock`: testar lock adquirido, lock de PID ativo (levanta `IndexLockError`),
  lock de PID inexistente (reclaim automatico), release no `finally`.
- `upsert_deps_batch`: mock do `pipeline()` Redis; verificar que N simbolos resultam em
  exatamente 1 `pipe.execute()`.
- `delete_by_file` (existente): mock do `AsyncQdrantClient.delete`; verificar filtro
  `file_path`.
- `index_path` com `FileCache` mockado: verificar que arquivos com sha1 identico sao
  pulados; arquivos alterados passam pelo ciclo sentinel-pending / delete_by_file /
  upsert / sentinel-done.

---

## Verificacao end-to-end

1. **Skip incremental:** indexar um repo; sem modificar nenhum arquivo, re-rodar `axon index`;
   verificar que o output e `0 arquivos re-embeddados` (todos pulados pelo cache com
   `status='done'`).

2. **Reconcile orfao (D1 + D6):** indexar um arquivo Python de 5 funcoes; editar 3
   linhas antes da primeira funcao (antes: ids baseados em start_line mudavam; agora com
   D1 os ids sao estaveis); re-indexar; scroll Qdrant para esse `file_path`; count deve
   ser 5 (nao 10) independente do deslocamento de linha.

3. **Arquivo deletado:** indexar repo; deletar 1 arquivo; re-indexar; scroll Qdrant para
   o `file_path` deletado deve retornar 0 pontos. `file_index` nao deve conter a entrada.

4. **Gitignore guard (D3 - teste de seguranca obrigatorio):** criar `.env` no repo com
   `SECRET=abc`; fazer `git add .env`; adicionar `.env` ao `.gitignore`; `axon index`;
   scroll Qdrant por `file_path` contendo `.env`; deve retornar 0 resultados. O arquivo
   nao pode aparecer no Qdrant em nenhuma circunstancia.

5. **Concorrencia:** lancar `axon index <repo>` e `python -m axon.hooks.git_event post-commit`
   via `subprocess` simultaneamente; apos ambos terminarem, scroll Qdrant e verificar
   ausencia de IDs duplicados; checar Redis por JSON invalido em chaves `dep:*`. Repetir
   20 vezes.

6. **Lockfile com PID:** durante um `axon index` em andamento (artificialmente lentificado
   via sleep em teste de integracao), tentar um segundo `axon index` no mesmo repo; o
   segundo deve sair com aviso `outro processo indexando` e exit 0 (sem stacktrace).
   Apos o primeiro terminar, criar lock com PID invalido (ex: 99999999) e verificar que
   o proximo `axon index` reclama o lock e prossegue normalmente.

7. **Crash-safety (D2):** simular crash entre o sentinel `status='pending'` e o
   `status='done'` (ex: `KeyboardInterrupt` no meio do upsert); verificar que na proxima
   execucao o arquivo e re-indexado completo e o `status` fica `done`.

8. **Normalizacao de path no Windows:** criar um arquivo cujo path seria emitido com `\\`
   pelo `Path` do Windows; verificar que o lookup no `file_index` e no Qdrant usa a forma
   posix e encontra o registro correto.

9. **Migracao one-shot:** apos purge das colecoes legadas e reindex completo via
   blue/green, executar `axon search_code "funcao conhecida"`; deve retornar hits dos 9
   repos em ctx `knowledge`.

---

## Testes

### Unitarios

- `test_file_cache.py`:
  - `test_get_all_sha1s_empty`: nenhum entry no cache -> dict vazio.
  - `test_get_all_sha1s_filters_done`: entries com `status='pending'` nao aparecem.
  - `test_get_all_sha1s_hit`: arquivo no cache com `status='done'` -> sha1 correto.
  - `test_set_entry_upsert`: segunda chamada com sha1 diferente atualiza a linha.
  - `test_set_entry_pending_then_done`: set pending, depois done, apenas done aparece
    em `get_all_sha1s`.
  - `test_delete_entry`: entrada removida, ausente em `get_all_sha1s`.
  - `test_list_entries_filters_by_ctx`: entries de ctx `work` nao aparecem em `knowledge`.
  - `test_path_normalization`: path com backslash armazenado e lido como posix.

- `test_index_lock.py`:
  - `test_acquire_releases_on_exit`: lockfile removido apos bloco.
  - `test_acquire_raises_if_pid_alive`: segundo `acquire` com PID ativo levanta
    `IndexLockError`.
  - `test_acquire_reclaims_stale_lock`: lock com PID inexistente e reclamado e indexacao
    prossegue normalmente.
  - `test_acquire_releases_on_exception`: lockfile removido mesmo com excecao interna.
  - `test_index_lock_windows.py` (Windows 11 / R7 5800X3D - hipotese H7): verificar que
    `_pid_alive` retorna False para PID de processo terminado e que o reclaim ocorre
    corretamente. Marcar com `@pytest.mark.skipif(sys.platform != 'win32', ...)`.
    OBRIGATORIO antes de declarar reclaim como suportado em Windows.

- `test_upsert_deps_batch.py`:
  - `test_batch_single_pipeline_call`: N simbolos resultam em exatamente 1 `pipe.execute()`.
  - `test_empty_batch_no_op`: lista vazia nao chama `pipeline()`.
  - `test_partial_failure_no_corrupt_data`: falha simulada no `pipe.execute()` nao deixa
    dados malformados em chaves `dep:*` existentes.

- `test_chunk_id_stable.py`:
  - `test_id_stable_after_line_shift`: mesmo simbolo com `start_line` diferente produz
    id identico (D1 - occurrence_index e usado, nao start_line).
  - `test_id_disambiguates_overloads`: dois metodos com mesmo nome no mesmo arquivo
    recebem ids distintos (occurrence_index 0 vs 1).

### Integracao

- `test_incremental_skip.py`: index + re-index sem mudancas; mock do embedder; assertar
  que `engine.embed()` nao foi chamado na segunda rodada.
- `test_orphan_reconcile.py`: index -> editar arquivo -> re-index -> scroll Qdrant;
  count identico antes/depois (nao acumula).
- `test_deleted_file_cleanup.py`: index -> deletar arquivo -> re-index -> scroll = 0.
- `test_gitignore_exclusion.py`: arquivo commitado e depois adicionado ao .gitignore ->
  index -> scroll = 0. (TESTE DE SEGURANCA - obrigatorio, nao pode ser pulado por
  coverage.)
- `test_crash_safety.py`: sentinel `pending` sobrevive a crash simulado; proxima execucao
  re-indexa e seta `done`.
- `test_cross_ctx_no_false_positive.py`: arquivo existente em `knowledge` mas ausente
  em `work` nao e deletado do Qdrant de `knowledge` durante index de `work`.

### Regressao de recall

- `test_recall_guard.py`: carrega `tests/recall/baseline.json`; roda harness de 20 queries
  contra Qdrant real (testcontainers); `compare_benchmark_runs(current, baseline)`;
  `assert len(report.regressions) == 0` e `assert summary.score >= 0.90`.
  O threshold `min_score` por query e lido de `score_calibration.json`.

### Cobertura

Minimo 80% nos modulos novos/alterados: `file_cache.py`, `index_lock.py`, modulo
`graph_store.py` nas funcoes adicionadas, `pipeline.py` nos caminhos de skip/reconcile/
delete/sentinel.

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
- **Streaming de `build_dependency_records`:** a hipotese de que a acumulacao de
  `graph_chunks` causa pico de RAM esta registrada como H6 e sera confirmada ou refutada
  em Phase 0. A correcao (stream por arquivo) pertence ao Pilar A.
- **Suporte a novos languages (Rust, Go, Bash):** escopo do chunker, nao deste pilar.
- **Rollback de migration SQLite:** o sistema de migration atual nao tem down-migration;
  adicionar esse mecanismo e escopo separado de infra de DB.
