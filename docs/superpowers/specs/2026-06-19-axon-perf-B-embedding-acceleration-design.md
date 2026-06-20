# Design: Aceleracao de Embedding (Spec B - Pilar de Performance)

Data: 2026-06-19
Status: rascunho - aguardando gate de medicao (Phase 0)
Escopo: **B** (embedding). Pilar "incremental cache" = Spec C (file_index SQLite, reconcile, lockfile). Pilar "parse-once / chunk-cap" = Spec A (chunk-size cap, parse-once linearization, git ls-files walk). Este spec cobre: provider auto-detectado por maquina, batching com teto de tokens, thread tuning do onnxruntime, chunk-id estavel, crash-safety via pending sentinel, escopo de indexacao restrito a arquivos rastreados pelo git. Aceleracao somente apos medicao provar necessidade (YAGNI).

---

## Contexto

O AXON usa `fastembed v0.8.0` com `TextEmbedding` sem nenhum argumento de `providers`
(engine.py:56-62). O onnxruntime escolhe o provider por padrao - na pratica, apenas
`CPUExecutionProvider` esta disponivel hoje no desktop com RTX 4070 Ti, pois o wheel
instalado e a build CPU-only (hipotese a verificar - ver Premissas). No Mac M1 Pro,
CoreML tambem nao e passado explicitamente.

Fatos verificados no codigo:
- `engine.py:23-26` - deteccao de plataforma: `Darwin+arm64` -> bge-small (384-dim, ~33 MB);
  todos os outros -> bge-base (768-dim, ~110 MB). Sem deteccao de GPU.
- `engine.py:56-62` (`_ensure_model`) - `TextEmbedding(model_name=..., cache_dir=...)`;
  nenhum argumento `providers` passado.
- `engine.py:17-20` (`FASTEMBED_MODEL_DIMS`) - mapa estatico de dimensoes; limpo, sem refactor.
- `pipeline.py:29` - `_BATCH_SIZE = 400` (chunks por flush no Qdrant).
- `pipeline.py:170` - `engine.embed([c.content for c in chunks])` chamado por arquivo;
  lotes reais sao 5-30 chunks por arquivo tipico, nao 400.
- `pipeline.py:28` - `_FILE_HASH_CACHE: dict[str, str] = {}` - in-process, nao persiste
  entre execucoes (problema tratado no Spec C via file_index SQLite, referenciado aqui apenas como contexto).
- `pipeline.py:141` - `graph_chunks: list[Chunk] = []` acumula todos os chunks de todos
  os arquivos antes de chamar `build_dependency_records` (linha 197); HIPOTESE: possivelmente
  responsavel pelo pico de RSS observado (ver P3 - hipotese a verificar em Phase 0).
- `pipeline.py:206-211` - `_chunk_id` usa `f"{path}::{chunk.symbol}::{chunk.start_line}"`,
  o que cria ids instAveis: editar linhas acima de um simbolo muda seu id e cria pontos
  orfaos no Qdrant. Correcao via D1 (uuid5 com occurrence_index).
- `vector_store.py:24` - `COLLECTIONS = list(VALID_CONTEXTS)` - constante existente.
- `vector_store.py:163` - `delete_by_file(ctx, file_path)` - metodo existente; sera
  reutilizado (D4); nenhum metodo de delete novo sera criado.

Medicao preliminar existente (1 amostra, maquina sob carga; nao e baseline definitivo):
- Load do modelo fastembed: ~0.6 s
- Chunks curtos (funcoes pequenas): ~240 chunks/s
- Chunks longos (~300 tokens): ~3 chunks/s

Esses numeros mostram que throughput e dominado pelo tamanho do chunk, nao pelo I/O.
O Spec A de init-code-embedding (2026-06-19-axon-init-code-embedding-design.md) ja
identificou o risco de memoria e throughput como "passe de perf dedicado - a confirmar".
Este spec e esse passe.

---

## Premissas (ledger - verificar antes de qualquer mudanca de codigo)

| # | Premissa | Verificacao barata | Consequencia se falsa |
|---|---|---|---|
| P1 | RTX 4070 Ti presente mas onnxruntime instalado sem CUDA (wheel CPU-only) | `python -c "import onnxruntime as ort; print(ort.get_available_providers())"` + `pip show onnxruntime onnxruntime-gpu` no desktop. Se `CUDAExecutionProvider` ausente, confirmado. | Se CUDA ja disponivel: pular troca de wheel, ir direto para passar `providers` kwarg. |
| P2 | `fastembed 0.8.0` aceita kwarg `providers` em `TextEmbedding()` | `python -c "from fastembed import TextEmbedding; import inspect; print(inspect.signature(TextEmbedding.__init__))"` + checar changelog 0.8.0. | Se ausente: exige upgrade de versao ou injecao via `SessionOptions` do onnxruntime diretamente. |
| P3 | O pico de RSS observado durante indexacao pode vir da lista `graph_chunks` acumulando todos os chunks antes de `build_dependency_records` (pipeline.py:141-196), de outros fatores como o proprio load do modelo, ou de ambos | Snapshot de RSS com `psutil` antes/apos `graph_chunks.extend()` (pipeline.py:187) e antes/apos `_ensure_model()` em um repo medio (~1.000 arquivos). Registrar em `benchmarks/phase0_profile.json`. | Se culpa e o modelo: a solucao e descarregar o modelo apos o embedding, nao stremar graph_chunks. Hipotese nao confirmada ate Phase 0 completar. |
| P4 | onnxruntime no R7 5800X3D usa menos que os 16 threads disponiveis (cores ociosos durante embed) | `psutil.cpu_percent(percpu=True)` em paralelo com um index. Checar default de `ort.SessionOptions().intra_op_num_threads`. | Se ja usa todos os cores: thread tuning nao ganha nada. |
| P5 | GPU (se disponivel via CUDAExecutionProvider) e mais rapida que CPU para os lotes reais do AXON (5-30 chunks por arquivo tipico) | SEM medir nao afirmar. Apos swap do wheel: comparar `providers=['CUDAExecutionProvider','CPUExecutionProvider']` vs `['CPUExecutionProvider']` com o corpus sintetico de 500 funcoes. GPU so ganha se amortiza overhead de transferencia PCIe para esses tamanhos de lote. Para lotes pequenos, GPU frequentemente perde por overhead de lancamento PCIe; batching cross-arquivo pode ser necessario para que GPU seja vantajosa - isso esta fora do escopo deste spec. | Se CPU e mais rapida para lotes pequenos: nao usar GPU por padrao para arquivos pequenos; usar batching maior para ativar GPU. |
| P6 | `rglob` sem poda de diretorios e contribuidor relevante ao wall time total | `time python -c "from pathlib import Path; import time; t=time.perf_counter(); files=list(Path('<vault>').rglob('*')); print(len(files), time.perf_counter()-t)"` vs `time git ls-files --cached | wc -l`. | Se rglob < 1 s: bottleneck e embedding, nao I/O (poda vira YAGNI). |

---

## Criterios de sucesso (numericos, por maquina)

| Metrica | Alvo R7 5800X3D | Alvo M1 Pro | Como medir |
|---|---|---|---|
| Full-index wall time (9 repos, hash-cache frio) | <= 5 min | <= 8 min | `time axon index <vault_root>` em processo fresco; mediana de 3 runs; sem pre-warm do modelo |
| Refresh incremental (1 arquivo alterado, 10-50 chunks) | <= 10 s | <= 15 s | Alterar 1 arquivo .py ja indexado, medir wall time do `axon index <repo>`; 5 arquivos de tamanhos variados (10/20/30/40/50 chunks); todos devem passar |
| Hook pos-commit (20 arquivos .py/.java) | <= 30 s | <= 45 s | Commit tocando 20 arquivos; medir wall time do `python -m axon.hooks.git_event post-commit`; 3 runs, tomar maximo |
| Peak RSS durante full index (9 repos) | <= 2 GB | <= 1.5 GB | `psutil.Process().memory_info().rss` amostrado a cada 2 s; tamanho do modelo conta (~110 MB desktop, ~33 MB mac) |
| Throughput embedding (chunks/s end-to-end: chunk+embed+upsert) | definido apos Phase 0 (baseline ~240 chunks/s curtos; meta condicional a GPU vs CPU-only - ver nota abaixo) | >= 200 chunks/s | Corpus sintetico fixo de 500 funcoes Python (15-30 linhas cada); `total_chunks / wall_seconds` |
| Recall Top-1 (query -> arquivo correto) | >= 0.90 no golden set de 20 queries | >= 0.90 | Ver secao "Guarda de Qualidade" abaixo |
| Recall Top-3 (arquivo correto nos 3 primeiros hits) | >= 0.95 no golden set | >= 0.95 | Mesma harness |
| Exclusao de arquivos gitignored (seguranca) | 0 pontos Qdrant com `file_path` de arquivo gitignored | 0 pontos | Apos indexar repo com `.env` e `secrets.json` no .gitignore, scroll Qdrant e assert vazio |
| Correcao de chunk-id apos reconcile por arquivo | 0 pontos orfaos apos editar 3 linhas acima de um simbolo | 0 pontos | Ver secao "Reconcile por Arquivo" |

**Nota sobre o alvo de throughput no desktop**: a unica medicao disponivel e ~240 chunks/s
(curtos, 1 amostra, maquina sob carga). O alvo numerico final para o desktop sera fixado
apos Phase 0 com baseline em condicoes controladas. Se GPU estiver disponivel e ganho
for confirmado (P5), um alvo mais alto e justificavel. Em modo CPU-only, o alvo e
definido como melhoria mensuravel sobre o baseline Phase 0, nao como numero absoluto de 300/s.

---

## Gate de Medicao - Phase 0 (bloqueante)

**Nenhuma mudanca de codigo de indexacao e permitida ate todas as condicoes abaixo serem verdadeiras.**

### Condicoes do gate (todas devem ser satisfeitas)

1. **Throughput baseline capturado**: rodar `index_path` no corpus sintetico de 500 funcoes
   nas duas maquinas; registrar chunks/s e wall time em `benchmarks/phase0_baseline.json`.

2. **Peak RSS baseline capturado**: rodar full index dos 9 repos nas duas maquinas com
   `psutil` amostrado a cada 2 s; registrar pico em `benchmarks/phase0_baseline.json`.
   - **Bloqueio de emergencia**: se RSS > 8 GB no desktop, rodar o probe de P3 imediatamente:
     medir RSS antes/apos `graph_chunks.extend()` (pipeline.py:187) e antes/apos
     `_ensure_model()` para identificar qual componente e dominante. Se a hipotese P3
     se confirmar (lista `graph_chunks` e responsavel), implantar streaming de
     `build_dependency_records` por arquivo (Spec C, pipeline.py:197) como item 0 da Fase 1, antes de
     qualquer otimizacao de embedding. Atencao: streaming por arquivo perderia arestas cross-arquivo
     de `called_by`; esse trade-off e responsabilidade do Spec C avaliar.

3. **Bottleneck identificado e rankiado**: executar probes das premissas P4 (threads), P6
   (rglob), P3 (RSS) e "large chunks" no desktop; registrar numeros brutos em
   `benchmarks/phase0_profile.json`. Pelo menos um bottleneck confirmado com numero medido.
   Condicao de saida: `benchmarks/phase0_profile.json` contem valores nao-nulos para
   `ort_default_threads`, `rglob_wall_sec`, `large_chunks_found` e anotacao de RSS antes/apos
   a extensao de `graph_chunks`.

4. **Disponibilidade de GPU confirmada ou descartada**: executar probe de P1 e P2 no
   desktop; registrar em `benchmarks/phase0_baseline.json` como `"desktop_gpu_available":
   true/false`. Se `false`, GPU e removida do plano e a Fase 1 cobre apenas CPU threading.

5. **Stale points Qdrant confirmados**: em um repo ja indexado, rodar:
   ```python
   from qdrant_client import QdrantClient
   client = QdrantClient("http://localhost:6333")
   # Editar 3 linhas acima de um simbolo sem alterar o simbolo
   # Re-indexar o arquivo
   # Contar pontos com file_path do arquivo
   result = client.scroll(collection_name="knowledge",
                          scroll_filter=Filter(must=[FieldCondition(
                              key="file_path", match=MatchValue(value="<arquivo_teste>"))]),
                          limit=100)
   print(len(result[0]))  # deve ser == numero de simbolos no arquivo, sem duplicatas
   ```
   Registrar em `benchmarks/phase0_baseline.json` como `"stale_qdrant_points_confirmed": bool`.
   Se `true`, stable chunk-id (D1) + reconcile por arquivo (D6) viram item prioritario na Fase 1.

6. **Recall baseline capturado**: rodar a harness de 20 queries contra o indice atual
   (sem nenhuma mudanca); registrar `BenchmarkRunSummary` em `tests/recall/baseline.json`.
   Top-1 e Top-3 devem ser >= 0.80 para haver baseline valido. Abaixo disso = bug pre-
   existente a corrigir antes do overhaul.

### Output obrigatorio do Phase 0

`benchmarks/phase0_baseline.json`:
```json
{
  "desktop_full_index_wall_sec": null,
  "desktop_peak_rss_mb": null,
  "desktop_chunks_per_sec": null,
  "mac_full_index_wall_sec": null,
  "mac_peak_rss_mb": null,
  "mac_chunks_per_sec": null,
  "desktop_gpu_available": null,
  "rglob_wall_sec": null,
  "redis_loop_ms_per_100_symbols": null,
  "ort_default_threads": null,
  "large_chunks_found": null,
  "stale_qdrant_points_confirmed": null,
  "recall_top1_baseline": null,
  "recall_top3_baseline": null
}
```

Este arquivo deve ser commitado antes de qualquer PR da Fase 1 ser aberto.

---

## Decisoes de Design

| Tema | Decisao |
|---|---|
| Chunk-id estavel (D1) | `_chunk_id` muda para `uuid5(NAMESPACE_URL, f"{file_path}::{symbol}::{occurrence_index}")` onde `occurrence_index` e o indice 0-based do simbolo dentro do arquivo (desambigua overloads e sub-chunks como `foo[0]/foo[1]`). `start_line` e REMOVIDO do id. Editar linhas acima de um simbolo nao cria pontos orfaos. |
| Crash-safety via pending sentinel (D2) | A tabela `file_index` recebe coluna `status`. Ao re-indexar: (a) escrever row com `status='pending'` + novo sha ANTES de mutar Qdrant; (b) `delete_by_file` + upsert dos novos pontos; (c) setar `status='done'`. Em qualquer run, row `'pending'` e tratada como dirty e re-indexada. Migracao one-shot dos 9 repos ja indexados usa BLUE/GREEN (nova colecao Qdrant, gate de recall, alias swap so se passou). Runs incrementais normais NAO usam blue/green. |
| Escopo de walk = tracked-only + check-ignore (D3) | Substituir `rglob` (pipeline.py:70) por `git ls-files --cached` (SEM --others). Filtrar cada path via `git check-ignore` para excluir arquivos que foram commitados e depois gitignored. Arquivos nao-rastreados requerem `git add` antes de serem indexados. Garantia de seguranca: "nenhum arquivo gitignored e jamais embedado" - adicionar teste (commitar .env, adicionar ao .gitignore, assert de 0 pontos no Qdrant). Fallback para rglob se nao for repo git. |
| Reutilizar delete existente (D4) | NAO adicionar nenhum metodo de delete novo. `vector_store.py:163` ja tem `delete_by_file(ctx, file_path)`. Usar via loop sobre `COLLECTIONS` (vector_store.py:24) para delete all-context. Remover qualquer proposta de `delete_file_points`, `delete_by_file_path` ou `_collections()`. |
| 14 GB e hipotese, nao fato (D5) | A causa provavel do pico de RSS observado e a lista `graph_chunks` acumulando todos os chunks de todos os arquivos em `index_path` (pipeline.py:141) antes de `build_dependency_records` rodar no final (pipeline.py:197). NAO afirmar causalidade ate Phase 0 confirmar via medicao de RSS antes/apos `graph_chunks.extend()`. A correcao (stream `build_dependency_records` por arquivo) pertence ao Spec C. Nota: streaming por arquivo perderia arestas cross-arquivo de `called_by` que `build_dependency_records` agrega ao receber todos os chunks juntos; esse trade-off deve ser avaliado no Spec C. |
| Reconcile nao e gate da hash-skip (D6) | Com ids estaveis (D1), arquivos nao alterados mantem pontos validos mesmo que suas linhas tenham mudado (nenhum re-index necessario em hash hit). Um arquivo alterado (hash miss) executa `delete_by_file` + re-upsert, que limpa simbolos deletados/renomeados. Este e o mecanismo real de resolucao do problema de orfaos. O spec nao afirma que delete-por-arquivo so dispara em hash miss como solucao para orfaos - a solucao e D1 + D6 juntos. |
| Ordem de aceleracao | Medicao primeiro (Phase 0) -> stable chunk-id + reconcile (D1/D6) -> cap de chunk size (YAGNI + qualidade, condicional a `large_chunks_found > 0`) -> provider nativo da biblioteca -> thread tuning -> so entao considerar pool multiprocessing se ainda necessario |
| GPU | Condicional: so se P1 + P2 confirmadas E medicao comparativa (P5) mostrar ganho real para os lotes do AXON. Para lotes de 5-30 chunks, GPU frequentemente perde por overhead PCIe; batching cross-arquivo pode ser necessario para amortizar esse custo e esta fora do escopo. |
| CoreML (Mac) | Mesmo padrao: passar `providers=['CoreMLExecutionProvider','CPUExecutionProvider']` via kwarg so se fastembed expoe o kwarg (P2) e medicao mostrar beneficio |
| Thread tuning | Usar `SessionOptions.intra_op_num_threads` via `providers_options` (preferido). `OMP_NUM_THREADS` via `os.environ` em `_ensure_model` e um no-op porque onnxruntime le essa variavel no momento do import, nao da instanciacao da sessao. Ver nota detalhada na secao de implementacao. So apos P4 confirmar subutilizacao. |
| Pool de multiprocessing hand-rolled | YAGNI: nao construir. fastembed/onnxruntime ja sao multi-thread internamente. So justificavel se library-native nao atingir os alvos apos medicao. |
| Batching por comprimento (length-bucketed) | Agrupar chunks por faixa de token-count antes de passar para `embed()` para reduzir padding interno do onnxruntime. Teto de tokens por lote (token budget) para evitar pico de RSS. Condicional a `large_chunks_found > 0` no Phase 0 (YAGNI para arquivos tipicos de 5-30 chunks). |
| Teto de tokens por lote | `MAX_BATCH_TOKENS = 8192` como default (ajustavel por env var `AXON_MAX_BATCH_TOKENS`). Se um unico chunk exceder o teto, ele vai em lote proprio (nao descartado). |
| Cap de chunk size | `_MAX_CHUNK_LINES = 80` tokens estimados por chunk (mesmo valor do Java, chunker.py:37). Condicional a `large_chunks_found > 0` no Phase 0. Impacta recall positivamente. Verificar recall guard antes e apos. |
| Flush atomico delete+upsert por arquivo | O delete (`delete_by_file`) e o upsert dos novos pontos do mesmo arquivo sao executados dentro da iteracao por arquivo, sem esperar pelo flush do `_BATCH_SIZE`. Isso elimina a janela de busca onde um arquivo nao tem pontos. Ver secao de implementacao para detalhe. |
| _FILE_HASH_CACHE persistente | Fora de escopo deste spec (coberto pelo Spec C). |

---

## Componentes e mudancas

### 1. `axon/embedder/pipeline.py` - chunk-id estavel (D1)

`_chunk_id` atual (pipeline.py:206-211) usa `start_line` no key, o que torna o id
instavel quando linhas acima do simbolo sao editadas.

Nova implementacao:
```python
def _chunk_id(path: Path, chunk: Chunk, occurrence_index: int) -> str:
    """Stable ID: uuid5 de file_path::symbol::occurrence_index.
    occurrence_index e o indice 0-based do simbolo dentro do arquivo,
    desambiguando overloads e sub-chunks (foo[0], foo[1]).
    start_line NAO entra no key - editar linhas acima nao muda o id."""
    import uuid
    key = f"{path}::{chunk.symbol}::{occurrence_index}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, key))
```

**Todos os call sites de `_chunk_id` devem ser atualizados para a nova assinatura com `occurrence_index`.** Ha dois call sites em pipeline.py: `index_path` (linha 173) e `ingest_file` (linha 109). Ambos devem passar `occurrence_index` como indice 0-based dentro da lista de chunks do arquivo. O arquivo `scripts/index_once.py` nao chama `_chunk_id` diretamente e nao requer alteracao para D1.

O chamador em `index_path` (pipeline.py:173) passa `occurrence_index` calculado como
`enumerate`-index dentro da lista de chunks do arquivo atual:
```python
vector_chunks = [
    VectorChunk(
        id=_chunk_id(file_path, c, i),
        ...
    )
    for i, (c, vec) in enumerate(zip(chunks, vectors))
]
```

O mesmo padrao de `enumerate` deve ser aplicado ao call site em `ingest_file` (pipeline.py:109),
passando `occurrence_index=i` como terceiro argumento para `_chunk_id`.

Arquivo afetado: `C:/Users/samde/dev/axon/src/axon/embedder/pipeline.py` (funcao `_chunk_id`, call site em `index_path` linha 173, call site em `ingest_file` linha 109)

### 2. `axon/store/vector_store.py` - reutilizar delete_by_file (D4)

O metodo `delete_by_file(ctx, file_path)` ja existe em `vector_store.py:163`.
NAO criar `delete_file_points`, `delete_by_file_path`, nem `_collections()`.

Para deletar pontos de um arquivo em todos os contexts, usar `COLLECTIONS` (vector_store.py:24):
```python
# Em pipeline.py, dentro do loop de arquivos, antes do upsert
from axon.store.vector_store import COLLECTIONS
for ctx in COLLECTIONS:
    await store.delete_by_file(ctx, str(file_path))
```

O flush atomico e feito DENTRO da iteracao por arquivo - nao aguardar o `_BATCH_SIZE`:
```python
# pipeline.py - loop por arquivo (hash miss confirmado)
# 1. Escrever pending sentinel no file_index (D2)
# 2. Delete atomico em todos os contexts
for ctx in COLLECTIONS:
    await store.delete_by_file(ctx, str(file_path))
# 3. Upsert imediato dos novos pontos (sem defer para _BATCH_SIZE)
await store.upsert_batch(vector_chunks)
# 4. Marcar status='done' no file_index (D2)
```

O `_BATCH_SIZE=400` existente continua controlando o flush de arquivos que nao passam
pelo reconcile (arquivos novos no primeiro index), mas arquivos re-indexados fazem
flush atomico por arquivo.

Arquivo afetado: `C:/Users/samde/dev/axon/src/axon/store/vector_store.py` (sem mudancas de metodo)
Arquivo afetado: `C:/Users/samde/dev/axon/src/axon/embedder/pipeline.py`

### 3. `axon/embedder/pipeline.py` - crash-safety pending sentinel (D2)

A tabela `file_index` do SQLite (Spec C) recebe coluna `status TEXT NOT NULL DEFAULT 'done'`.

Fluxo de re-indexacao (hash miss):
```
(a) INSERT OR REPLACE INTO file_index(file_path, sha1, status) VALUES (?, ?, 'pending')
(b) DELETE pontos Qdrant (via delete_by_file loop sobre COLLECTIONS)
(c) UPSERT novos pontos Qdrant
(d) UPDATE file_index SET status='done' WHERE file_path=?
```

Na inicializacao de qualquer run:
```python
# Tratar rows 'pending' como dirty - re-indexar mesmo com hash igual
pending = db.execute("SELECT file_path FROM file_index WHERE status='pending'").fetchall()
for row in pending:
    _FILE_HASH_CACHE.pop(row["file_path"], None)  # forca re-index
```

Migracao one-shot dos 9 repos ja indexados usa BLUE/GREEN:
- Criar nova colecao Qdrant com sufixo `_v2` (ou usar alias Qdrant)
- Indexar tudo com ids D1 na nova colecao
- Rodar gate de recall (Top-1 >= 0.90, Top-3 >= 0.95) contra a nova colecao
- Se passou: `client.update_collection_aliases(...)` para promover `_v2` como colecao ativa
- Se falhou: manter colecao antiga; investigar regressao antes de promover

Arquivo afetado: `C:/Users/samde/dev/axon/src/axon/embedder/pipeline.py`

### 4. `axon/embedder/pipeline.py` - escopo de walk tracked-only (D3)

Substituir `rglob` em `iter_supported_files` (pipeline.py:70) por `git ls-files --cached`.
Filtrar via `git check-ignore` para excluir arquivos gitignored mesmo que commitados.

```python
import subprocess

def _git_ls_files(target: Path) -> list[Path] | None:
    """Retorna lista de arquivos rastreados via git ls-files --cached.
    Nao inclui arquivos nao-rastreados (--others excluido por seguranca).
    Retorna None se nao for repo git."""
    try:
        result = subprocess.run(
            ["git", "-C", str(target), "ls-files", "--cached"],
            capture_output=True, text=True, check=True
        )
        candidates = [target / line for line in result.stdout.splitlines() if line]
        # Filtrar gitignored (arquivos que foram commitados e depois ignorados)
        return [p for p in candidates if not _is_gitignored(target, p)]
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None

def _is_gitignored(repo_root: Path, path: Path) -> bool:
    """True se git check-ignore classifica o path como ignorado."""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "check-ignore", "--quiet", str(path)],
            capture_output=True
        )
        return result.returncode == 0
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False

def iter_supported_files(
    target: Path,
    *,
    languages: set[str] | None = None,
) -> Iterable[Path]:
    if target.is_file():
        language = _language_for_suffix(target.suffix)
        if language and (languages is None or language in languages):
            yield target
        return

    git_files = _git_ls_files(target)
    candidates = git_files if git_files is not None else _rglob_fallback(target)
    for path in candidates:
        if any(part in EXCLUDED_DIR_NAMES for part in path.parts):
            continue
        language = _language_for_suffix(path.suffix)
        if path.is_file() and language and (languages is None or language in languages):
            yield path

def _rglob_fallback(target: Path) -> Iterable[Path]:
    """Fallback para rglob quando fora de repo git. Sem mudanca de logica."""
    return target.rglob("*")
```

Arquivo afetado: `C:/Users/samde/dev/axon/src/axon/embedder/pipeline.py`

### 5. `axon/embedder/engine.py` - provider auto-detectado

**Dependencias de premissas**: P1 (GPU disponivel), P2 (fastembed aceita providers), P4 (threads).

Nova funcao `_detect_providers() -> list[str]`:
```python
# engine.py - nova funcao, chamada UMA vez em _ensure_model()
def _detect_providers() -> list[str]:
    import onnxruntime as ort
    available = set(ort.get_available_providers())
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        if "CoreMLExecutionProvider" in available:
            return ["CoreMLExecutionProvider", "CPUExecutionProvider"]
    elif "CUDAExecutionProvider" in available:
        return ["CUDAExecutionProvider", "CPUExecutionProvider"]
    return ["CPUExecutionProvider"]
```

`_ensure_model()` atualizado (engine.py:56-62):
```python
def _ensure_model(self) -> TextEmbedding:
    if self._model is None:
        providers = _detect_providers()
        self._model = TextEmbedding(
            model_name=self.model_name,
            cache_dir=str(self.cache_dir),
            providers=providers,          # novo argumento - condicional a P2
        )
    return self._model
```

**Fallback seguro**: se `fastembed 0.8.0` nao aceita `providers` (P2 falsa), o kwarg e
omitido e o comportamento e identico ao atual. A verificacao de P2 define se o kwarg e
incluido ou se um upgrade de versao e necessario primeiro.

**Thread tuning - nota importante**: `os.environ["OMP_NUM_THREADS"] = ...` em `_ensure_model`
e um NO-OP porque onnxruntime le `OMP_NUM_THREADS` no momento do import do modulo, nao
da instanciacao da sessao. Definir a variavel apos o import nao tem efeito. A abordagem
correta e usar `SessionOptions.intra_op_num_threads` via `providers_options` do fastembed,
se o kwarg estiver disponivel (P2). Alternativa: setar `OMP_NUM_THREADS` antes do import
do onnxruntime (ex: via script de entrada, nao dentro do modulo engine.py). Antes de
implementar qualquer thread tuning, medir utilizacao de CPU com `psutil.cpu_percent(percpu=True)`
durante um index para confirmar subutilizacao (P4); se todos os cores ja estiverem ocupados,
tuning nao adiciona ganho.

Arquivo afetado: `C:/Users/samde/dev/axon/src/axon/embedder/engine.py`

### 6. `axon/embedder/pipeline.py` - batching com teto de tokens (condicional)

**Condicional a `large_chunks_found > 0` no Phase 0. Se todos os chunks sao < 512 tokens
(tipico para arquivos de 5-30 funcoes), esta secao e YAGNI e nao sera implementada.**

Constante de estimativa de tokens:
```python
# pipeline.py - apos linha 29
_MAX_BATCH_TOKENS: int = int(os.environ.get("AXON_MAX_BATCH_TOKENS", "8192"))
_MAX_CHUNK_TOKENS: int = int(os.environ.get("AXON_MAX_CHUNK_TOKENS", "512"))
_TOKENS_PER_CHAR: float = 0.35  # OVERESTIMATIVA DELIBERADA para safety cap de memoria de lote.
# Por que 0.35 e nao 0.25 (len//4)?
# vector_store.py:153 usa len(content)//4 (= 0.25 tokens/char) para orcamento de OUTPUT,
# onde subestimar e aceitavel (output fica dentro do budget). Aqui, o objetivo e oposto:
# garantir que o lote NAO exceda o budget de INPUT/memoria do onnxruntime. Subestimar tokens
# significaria montar lotes maiores do que o permitido, arriscando pico de RSS. Por isso usamos
# 0.35 (overestimativa conservadora: ~2.86 chars/token vs os ~4 chars/token medios do BPE).
# O valor exato deve ser calibrado empiricamente em Phase 0 com o corpus real do AXON.
```

Nova funcao `_estimate_tokens(text: str) -> int`:
```python
def _estimate_tokens(text: str) -> int:
    return max(1, int(len(text) * _TOKENS_PER_CHAR))
```

Funcao de batching por faixa de comprimento:
```python
def _make_token_bounded_batches(
    chunks: list[Chunk],
) -> list[list[Chunk]]:
    """Agrupa chunks em lotes que nao excedem _MAX_BATCH_TOKENS.
    Nunca descarta um chunk; se um chunk sozinho exceder o teto, vai
    em lote proprio."""
    batches: list[list[Chunk]] = []
    current: list[Chunk] = []
    current_tokens = 0
    for chunk in chunks:
        tokens = _estimate_tokens(chunk.content)
        if current and current_tokens + tokens > _MAX_BATCH_TOKENS:
            batches.append(current)
            current = []
            current_tokens = 0
        current.append(chunk)
        current_tokens += tokens
    if current:
        batches.append(current)
    return batches
```

`index_path` atualizado: em vez de `engine.embed([c.content for c in chunks])` por arquivo
(pipeline.py:170), dividir em sub-lotes via `_make_token_bounded_batches(chunks)` e
concatenar resultados. Esta mudanca so e introduzida se Phase 0 confirmar `large_chunks_found > 0`.

### 7. Cap de chunk size no chunker (qualidade + memoria) - condicional

**Condicional a `large_chunks_found > 0` no Phase 0 (mesmo gate que secao 6).**

Se `large_chunks_found > 0`, adicionar cap em `chunk_source` para Python e TypeScript
(Java ja tem `_MAX_CHUNK_LINES = 80` em chunker.py:37).

A funcao `_split_large_node` existente (chunker.py:217-241) recebe um `Node` do tree-sitter
e NAO pode ser reutilizada diretamente pelos chunkers de Python/TypeScript que ja constroem
`Chunk` antes de qualquer split. A implementacao correta e uma funcao separada que opera
sobre `Chunk` ja construido:

```python
# chunker.py - nova funcao, independente de tree-sitter
def _split_large_chunk_by_lines(chunk: Chunk, max_lines: int) -> list[Chunk]:
    """Divide um Chunk que excede max_lines em sub-chunks de linhas.
    Funciona sobre qualquer Chunk (Python, TypeScript, Java).
    O symbol de cada sub-chunk recebe sufixo [i] para desambiguacao
    (compativel com o occurrence_index de D1)."""
    lines = chunk.content.splitlines()
    if len(lines) <= max_lines:
        return [chunk]
    result: list[Chunk] = []
    for i in range(0, len(lines), max_lines):
        part_lines = lines[i : i + max_lines]
        result.append(
            Chunk(
                symbol=f"{chunk.symbol}[{i // max_lines}]",
                chunk_type=chunk.chunk_type,
                start_line=chunk.start_line + i,
                end_line=chunk.start_line + i + len(part_lines) - 1,
                content="\n".join(part_lines),
                file_path=chunk.file_path,
                language=chunk.language,
            )
        )
    return result
```

Essa funcao e chamada em `_chunk_python` e `_chunk_typescript` apos construir cada chunk,
substituindo chunks grandes pela lista de sub-chunks.

**Esta mudanca requer execucao completa da recall guard antes e apos** (ver secao abaixo).

Arquivo afetado: `C:/Users/samde/dev/axon/src/axon/embedder/chunker.py`

---

## Fluxo de dados (depois)

```
axon index <repo>
  |
  +-- startup: marcar rows 'pending' em file_index como dirty (D2)
  |
  +-- iter_supported_files(repo)
  |     git ls-files --cached (se repo git) - sem --others (D3)
  |     filtrar gitignored via git check-ignore (D3)
  |     OU rglob (fallback nao-git)
  |     exclui EXCLUDED_DIR_NAMES
  |
  +-- para cada arquivo (hash-cache check):
  |   hash hit + status='done': skip (id estavel via D1 = sem orfaos)
  |   hash miss OU status='pending':
  |     (a) file_index: status='pending' + novo sha (D2)
  |     chunks = chunk_source(source, language, path)
  |       [se large_chunks_found>0]: _split_large_chunk_by_lines por chunk grande
  |     [se large_chunks_found>0]: _make_token_bounded_batches(chunks)
  |     engine.embed(texts)
  |       TextEmbedding(providers=[CUDA|CoreML|CPU])  <- auto-detectado (P1+P2)
  |       SessionOptions.intra_op_num_threads         <- se P4 confirmada
  |     vector_chunks com ids D1 (uuid5 file::symbol::occurrence_index)
  |     DELETE atomico: delete_by_file(ctx, path) para cada ctx em COLLECTIONS (D4)
  |     UPSERT imediato: store.upsert_batch(vector_chunks)
  |     (d) file_index: status='done' (D2)
  |     graph_chunks.extend(chunks)
  |
  +-- flush final -> Qdrant (arquivos novos nao reconciliados)
  +-- build_dependency_records(graph_chunks) -> Redis
```

**Nota**: a causa do pico de RSS na lista `graph_chunks` e hipotese P3, a ser confirmada
em Phase 0. A correcao (stream por arquivo) pertence ao Spec C (D5).

---

## Guarda de Qualidade (Recall Guard)

A guarda de recall e um conjunto fixo de 20 pares `(query, expected_file, expected_symbol,
min_score)` armazenados em `tests/recall/golden_set.json`. Este arquivo e construido UMA
VEZ, com verificacao humana, ANTES de qualquer mudanca de chunker ou embedder. O golden
set NAO pode ser gerado automaticamente a partir do indice atual, pois isso codificaria
bugs pre-existentes como ground truth. Cada par deve ser verificado manualmente: a query
retorna o expected_file/symbol correto no indice atual? Se nao, e um bug a corrigir, nao
um item do golden set.

Distribuicao do golden set:
- 8 queries de funcoes Python
- 5 queries de metodos Java
- 4 queries de funcoes TypeScript
- 3 queries cross-arquivo / arquiteturais

Para cada query, o harness verifica:
- `check name='top_1_file_match'`: `hits[0].payload['file_path'] == expected_file`
- `check name='top_3_file_match'`: `expected_file` em `{hits[0..2].payload['file_path']}`
- `check name='min_score'`: `hits[0].score >= min_score` (piso: 0.70)
- `check name='symbol_match'`: `hits[0].payload['symbol'] == expected_symbol`

Implementacao reutiliza `BenchmarkCheck` e `BenchmarkResult` de
`src/axon/benchmark/contracts.py` (shape existente, sem modificacao).

O harness de recall (`RecallBenchmarkFixture`) usa embedding REAL (nao mockado) e Qdrant
real via `testcontainers[qdrant]` (ja no `pyproject.toml [dev]`). O corpus de referencia
e `src/axon/embedder/` + `src/axon/store/` (pequeno o suficiente para indexar em < 60 s).

**Gate de regressao** (`tests/recall/test_recall_guard.py`):
```python
def test_no_regression():
    baseline = load_json("tests/recall/baseline.json")
    current  = run_recall_harness()
    report   = compare_benchmark_runs(current, baseline)
    assert len(report.regressions) == 0, report.regressions
    assert current.score >= 0.90
```

Este teste bloqueia qualquer PR que regride recall. Baseline e atualizado explicitamente
(commit separado) so quando uma mudanca melhora comprovadamente a qualidade.

**Nota sobre o modelo e embedding no harness**: se a hipotese P3 for confirmada (lista
`graph_chunks` causa o pico de RSS), rodar o embedding em subprocess isolado via
`subprocess.run(['python', '-m', 'axon.bench.embed_worker', ...])` para manter o processo
do benchmark lean. O embed_worker recebe o corpus e retorna JSON com vetores via stdout.

---

## Unidades (isolamento)

| Unidade | Arquivo | Responsabilidade | Dependencias injetaveis |
|---|---|---|---|
| `_detect_providers()` | `engine.py` | Detecta providers disponiveis via onnxruntime | nenhuma (chamada `ort.get_available_providers()`) |
| `EmbedderEngine._ensure_model()` | `engine.py:56-62` | Instancia `TextEmbedding` com providers detectados | `_detect_providers()` mockavel |
| `_chunk_id(path, chunk, occurrence_index)` | `pipeline.py:206-211` | ID estavel via uuid5 sem start_line (D1) | nenhuma |
| `_estimate_tokens()` | `pipeline.py` | Estimativa de tokens por chunk (overestimativa intencional) | nenhuma |
| `_make_token_bounded_batches()` | `pipeline.py` | Agrupa chunks sem exceder teto de tokens (condicional) | nenhuma |
| `_split_large_chunk_by_lines()` | `chunker.py` | Divide Chunk grande em sub-chunks por linhas (condicional) | nenhuma |
| `_git_ls_files()` | `pipeline.py` | Lista arquivos rastreados via `git ls-files --cached` (D3) | subprocess mockavel |
| `_is_gitignored()` | `pipeline.py` | Verifica `git check-ignore` para exclusao de gitignored (D3) | subprocess mockavel |
| `iter_supported_files()` | `pipeline.py:59-75` | Walker com fallback rglob | `_git_ls_files`, `_is_gitignored` mockaveis |
| `VectorStore.delete_by_file(ctx, file_path)` | `vector_store.py:163` | Deleta pontos por file_path em um context (existente - D4) | Qdrant client mockavel |
| `RecallBenchmarkFixture` | `axon/benchmark/recall.py` (novo) | Executa queries no golden set | `VectorStore`, `EmbedderEngine` injetaveis |

---

## Verificacao end-to-end

1. **Provider detection**: apos swap do wheel (se P1 confirmada), `python -c
   "from axon.embedder.engine import _detect_providers; print(_detect_providers())"` deve
   imprimir `['CUDAExecutionProvider', 'CPUExecutionProvider']` no desktop.

2. **Throughput com provider correto**: corpus sintetico de 500 funcoes; throughput >=
   baseline Phase 0 + melhoria mensuravel no desktop. Se GPU nao ajudar (P5), alvo e
   cumprido via thread tuning ou batching. Target numerico final definido em Phase 0.

3. **RSS abaixo do teto**: `axon index <vault_root>` com psutil sampling; peak RSS <= 2 GB
   no desktop.

4. **Chunk-id estavel (D1)**: indexar arquivo, editar 3 linhas acima do primeiro simbolo
   (sem mudar o simbolo), re-indexar; `vector_store.scroll(filter=file_path)` deve retornar
   exatamente o mesmo conjunto de ids que antes da edicao.

5. **Reconcile funciona (D6)**: indexar arquivo, remover um simbolo, re-indexar;
   `vector_store.scroll(filter=file_path)` deve retornar numero de chunks reduzido (simbolo
   removido nao esta mais la), sem pontos orfaos.

6. **Gitignored nao entra (D3)**: repo com `.env` commitado e depois adicionado ao
   .gitignore; apos indexar, scroll Qdrant com filtro `file_path contains ".env"` deve
   retornar zero resultados.

7. **Recall guard passa**: `pytest tests/recall/test_recall_guard.py` com 0 regressoes;
   `current.score >= 0.90`.

8. **Wall time full index**: mediana de 3 runs com hash-cache frio; <= 5 min no desktop,
   <= 8 min no M1 Pro.

9. **Fallback sem GPU**: se `CUDAExecutionProvider` indisponivel, `_detect_providers()`
   retorna `['CPUExecutionProvider']` e `engine.embed()` funciona normalmente (sem erro).

10. **Crash-safety (D2)**: simular crash apos `status='pending'` + delete Qdrant mas antes
    do upsert; reiniciar; o arquivo deve ser re-indexado automaticamente (row pending
    detectada no startup).

---

## Testes

### Unit tests (sem model load, sem Qdrant)

- `test_chunk_id_stable_ignores_line_shift`: mesmo `_chunk_id` para chunk com `start_line=10`
  e `start_line=13`; so symbol e occurrence_index entram no key (D1).
- `test_chunk_id_disambiguates_overloads`: dois chunks com mesmo symbol, occurrence_index
  0 e 1; ids diferentes.
- `test_detect_providers_cuda`: mocka `ort.get_available_providers()` retornando
  `['CUDAExecutionProvider', 'CPUExecutionProvider']`; assert retorno inclui CUDA primeiro.
- `test_detect_providers_cpu_fallback`: mocka disponivel = `['CPUExecutionProvider']`;
  assert retorno = `['CPUExecutionProvider']`.
- `test_detect_providers_coreml_mac`: mocka `platform.system()='Darwin'`,
  `platform.machine()='arm64'`, disponivel inclui `CoreMLExecutionProvider`; assert
  retorno = `['CoreMLExecutionProvider', 'CPUExecutionProvider']`.
- `test_make_token_bounded_batches_teto` (condicional a large_chunks_found): 10 chunks de
  1000 tokens cada com teto 8192; verifica que batches resultantes nao excedem teto e
  soma de chunks = 10.
- `test_make_token_bounded_batches_chunk_gigante` (condicional): 1 chunk com 20.000 tokens;
  vai em lote proprio (nao descartado).
- `test_split_large_chunk_by_lines` (condicional): chunk de 200 linhas com max_lines=80;
  resultado = 3 sub-chunks com symbols foo[0], foo[1], foo[2].
- `test_estimate_tokens_overestimates`: texto de 100 chars; `_estimate_tokens` retorna >=
  35 (usa 0.35, nao 0.25).
- `test_git_ls_files_excludes_untracked`: subprocess mockado retornando apenas arquivos
  tracked; arquivos nao-rastreados nao aparecem.
- `test_git_ls_files_excludes_gitignored`: `_is_gitignored` mockado retornando True para
  `.env`; `.env` nao aparece no resultado.
- `test_iter_supported_files_fallback_rglob`: `CalledProcessError` levantado por
  `_git_ls_files`; `iter_supported_files` usa rglob.
- `test_delete_by_file_loops_collections`: `store.delete_by_file` mockado; verifica que
  e chamado uma vez por cada entry em `COLLECTIONS`.

### Integration tests (Qdrant via testcontainers, sem model load - embedder mockado)

- `test_reconcile_sem_orfaos_chunk_id_estavel`: indexar arquivo (3 chunks mockados),
  editar start_line simulando edicao de linhas acima (symbol nao muda), re-indexar;
  scroll deve retornar 3 pontos com ids identicos (D1).
- `test_reconcile_simbolo_removido`: indexar arquivo (3 chunks), re-indexar com 2 chunks
  (1 simbolo removido); scroll deve retornar 2 pontos, nao 3 (D6).
- `test_gitignored_excluido_do_indice`: repo git simulado com `.env` tracked e depois
  gitignored; apos `index_path`, scroll Qdrant nao deve retornar nenhum ponto com
  `file_path` contendo `.env` (D3).
- `test_pending_sentinel_reindex_apos_crash`: inserir row `status='pending'` no file_index;
  confirmar que startup marca o arquivo como dirty e re-indexa (D2).
- `test_flush_atomico_delete_upsert`: verificar que delete e upsert ocorrem dentro da
  iteracao do arquivo (nao adiados para o flush de _BATCH_SIZE); apos o loop do arquivo,
  scroll retorna novos pontos.
- `test_idempotencia_provider_fallback`: `_ensure_model` e chamado duas vezes; `TextEmbedding`
  e instanciada so uma vez (lazy init correto).

### Recall guard (real embedding + Qdrant container)

- `tests/recall/test_recall_guard.py::test_no_regression` - ver secao "Guarda de Qualidade".
- `tests/recall/test_recall_guard.py::test_top1_gte_090` - score Top-1 >= 0.90.
- `tests/recall/test_recall_guard.py::test_top3_gte_095` - score Top-3 >= 0.95.

Cobertura alvo: 80%+ nas unidades novas e alteradas.

---

## Fora de escopo

- Cache de hash persistente entre processos (SQLite, file_index, reconcile) - coberto pelo Spec C.
- Pipeline Redis para `upsert_deps` sequencial - coberto pelo Spec C.
- Re-parse duplo de AST em `graph_extractor.py` (parse-once / remocao de re-parse) - coberto pelo Spec A.
- Streaming de `build_dependency_records` por arquivo (fix do pico de RSS hipotetico) - Spec C (D5).
- Mudanca no conjunto de linguagens suportadas (`_LANGUAGE_MAP`).
- Pool de multiprocessing hand-rolled para embedding - YAGNI ate medicao provar necessidade.
- Batching cross-arquivo para amortizar overhead PCIe da GPU - fora de escopo; necessario
  confirmar via probe P5 antes de sequer considerar.
- Upgrade de modelo (bge-large, bge-m3, etc.) - mudaria dimensoes e invalidaria colecoes existentes.
- Markdown/text chunking com tree-sitter - fora deste overhaul de performance.
- Locking distribuido entre hook e index manual concorrentes - identificado como risco mas
  tratado no Spec C (lockfile, per-file reconcile) ou como trabalho futuro com asyncio.Lock.
- Migracao BLUE/GREEN continua apos o one-shot dos 9 repos - runs normais usam
  delete_by_file atomico por arquivo, nao blue/green (D2).
