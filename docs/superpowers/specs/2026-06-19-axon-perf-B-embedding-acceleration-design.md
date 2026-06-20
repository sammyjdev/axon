# Design: Aceleração de Embedding (Spec B - Pilar de Performance)

Data: 2026-06-19
Status: rascunho - aguardando gate de medicao (Phase 0)
Escopo: **B** (embedding). Pilar "linear" = Spec A (cache persistente + reconcile). Pilar "parallel" = Spec C (graph/redis pipeline). Este spec cobre: provider auto-detectado por maquina, batching com teto de tokens, thread tuning do onnxruntime. Aceleracao somente apos medicao provar necessidade (YAGNI).

---

## Contexto

O AXON usa `fastembed v0.8.0` com `TextEmbedding` sem nenhum argumento de `providers`
(engine.py:56-62). O onnxruntime escolhe o provider por padrao - na pratica, apenas
`CPUExecutionProvider` esta disponivel hoje no desktop com RTX 4070 Ti, pois o wheel
instalado e a build CPU-only (hipotese a verificar - ver Premissas). No Mac M1 Pro,
CoreML tambem nao e passado explicitamente.

Fatos verificados no codigo:
- `engine.py:23-26` - deteccao de plataforma: `Darwin+arm64` → bge-small (384-dim, ~33 MB);
  todos os outros → bge-base (768-dim, ~110 MB). Sem deteccao de GPU.
- `engine.py:56-62` (`_ensure_model`) - `TextEmbedding(model_name=..., cache_dir=...)`;
  nenhum argumento `providers` passado.
- `engine.py:17-20` (`FASTEMBED_MODEL_DIMS`) - mapa estatico de dimensoes; limpo, sem refactor.
- `pipeline.py:29` - `_BATCH_SIZE = 400` (chunks por flush no Qdrant).
- `pipeline.py:70` - `engine.embed([c.content for c in chunks])` chamado por arquivo;
  lotes reais sao 5-30 chunks por arquivo tipico, nao 400.
- `pipeline.py:28` - `_FILE_HASH_CACHE: dict[str, str] = {}` - in-process, nao persiste
  entre execucoes (problema tratado no Spec A, referenciado aqui apenas como contexto).
- `pipeline.py:141` - `graph_chunks: list[Chunk] = []` acumula todos os chunks de todos
  os arquivos antes de chamar `build_dependency_records` (linha 197); hipotetica causa do
  pico de 14 GB de RSS (hipotese a verificar - ver Premissas).

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
| P3 | O pico de 14 GB de RSS veio da lista `graph_chunks` acumulando todos os chunks antes de `build_dependency_records`, nao do load do modelo | Snapshot de RSS com `psutil` antes/apos `graph_chunks.extend()` e antes/apos `_ensure_model()` em um repo medio (~1.000 arquivos). | Se culpa e o modelo: a solucao e descarregar o modelo apos o embedding, nao stremar graph_chunks. |
| P4 | onnxruntime no R7 5800X3D usa menos que os 16 threads disponiveis (cores ociosos durante embed) | `psutil.cpu_percent(percpu=True)` em paralelo com um index. + `ort.SessionOptions().intra_op_num_threads` para ver o default. | Se ja usa todos os cores: thread tuning nao ganha nada. |
| P5 | GPU (se disponivel via CUDAExecutionProvider) e mais rapida que CPU para os lotes reais do AXON (5-30 chunks por arquivo) | SEM medir nao afirmar. Apos swap do wheel: comparar `providers=['CUDAExecutionProvider','CPUExecutionProvider']` vs `['CPUExecutionProvider']` com o corpus sintetico de 500 funcoes. GPU so ganha se amortiza overhead de transferencia PCIe para esses tamanhos de lote. | Se CPU e mais rapida para lotes pequenos: nao usar GPU por padrao para arquivos pequenos; usar batching maior para ativar GPU. |
| P6 | `rglob` sem poda de diretorios e contribuidor relevante ao wall time total | `time python -c "from pathlib import Path; import time; t=time.perf_counter(); files=list(Path('<vault>').rglob('*')); print(len(files), time.perf_counter()-t)"` vs `time git ls-files --cached --others --exclude-standard | wc -l`. | Se rglob < 1 s: bottleneck e embedding, nao I/O (poda vira YAGNI). |

---

## Criterios de sucesso (numericos, por maquina)

| Metrica | Alvo R7 5800X3D | Alvo M1 Pro | Como medir |
|---|---|---|---|
| Full-index wall time (9 repos, hash-cache frio) | <= 5 min | <= 8 min | `time axon index <vault_root>` em processo fresco; mediana de 3 runs; sem pre-warm do modelo |
| Refresh incremental (1 arquivo alterado, 10-50 chunks) | <= 10 s | <= 15 s | Alterar 1 arquivo .py ja indexado, medir wall time do `axon index <repo>`; 5 arquivos de tamanhos variados (10/20/30/40/50 chunks); todos devem passar |
| Hook pos-commit (20 arquivos .py/.java) | <= 30 s | <= 45 s | Commit tocando 20 arquivos; medir wall time do `python -m axon.hooks.git_event post-commit`; 3 runs, tomar maximo |
| Peak RSS durante full index (9 repos) | <= 2 GB | <= 1.5 GB | `psutil.Process().memory_info().rss` amostrado a cada 2 s; tamanho do modelo conta (~110 MB desktop, ~33 MB mac) |
| Throughput embedding (chunks/s end-to-end: chunk+embed+upsert) | >= 300 chunks/s | >= 200 chunks/s | Corpus sintetico fixo de 500 funcoes Python (15-30 linhas cada); `total_chunks / wall_seconds` |
| Recall Top-1 (query → arquivo correto) | >= 0.90 no golden set de 20 queries | >= 0.90 | Ver secao "Guarda de Qualidade" abaixo |
| Recall Top-3 (arquivo correto nos 3 primeiros hits) | >= 0.95 no golden set | >= 0.95 | Mesma harness |
| Exclusao de arquivos gitignored (seguranca) | 0 pontos Qdrant com `file_path` de arquivo gitignored | 0 pontos | Apos indexar repo com `.env` e `secrets.json` no .gitignore, scroll Qdrant e assert vazio |
| Correcao de chunk-id apos reconcile por arquivo | 0 pontos orfaos apos editar 3 linhas acima de um simbolo | 0 pontos | Ver secao "Reconcile por Arquivo" |

---

## Gate de Medicao - Phase 0 (bloqueante)

**Nenhuma mudanca de codigo de indexacao e permitida ate todas as condicoes abaixo serem verdadeiras.**

### Condicoes do gate (todas devem ser satisfeitas)

1. **Throughput baseline capturado**: rodar `index_path` no corpus sintetico de 500 funcoes
   nas duas maquinas; registrar chunks/s e wall time em `benchmarks/phase0_baseline.json`.

2. **Peak RSS baseline capturado**: rodar full index dos 9 repos nas duas maquinas com
   `psutil` amostrado a cada 2 s; registrar pico em `benchmarks/phase0_baseline.json`.
   - **Bloqueio de emergencia**: se RSS > 8 GB no desktop, confirmar a hipotese P3 (lista
     `graph_chunks`) e implantar streaming de `build_dependency_records` por arquivo como
     item 0 da Fase 1, antes de qualquer otimizacao de embedding.

3. **Bottleneck identificado e rankiado**: executar probes das premissas P4 (threads), P6
   (rglob), P3 (RSS) e "large chunks" no desktop; registrar numeros brutos em
   `benchmarks/phase0_profile.json`. Pelo menos um bottleneck confirmado com numero medido.

4. **Disponibilidade de GPU confirmada ou descartada**: executar probe de P1 e P2 no
   desktop; registrar em `benchmarks/phase0_baseline.json` como `"desktop_gpu_available":
   true/false`. Se `false`, GPU e removida do plano e a Fase 1 cobre apenas CPU threading.

5. **Stale points Qdrant confirmados**: probe de scroll descrito na premissa A10 do ledger
   principal em um repo ja indexado; registrar em `benchmarks/phase0_baseline.json` como
   `"stale_qdrant_points_confirmed": bool`. Se `true`, reconcile por arquivo vira item
   prioritario na Fase 1 (antes de aceleracao).

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

## Decisoes

| Tema | Decisao |
|---|---|
| Ordem de aceleracao | Medicao primeiro (Phase 0) → cap de chunk size (YAGNI + qualidade) → provider nativo da biblioteca (fastembed/onnxruntime) → thread tuning → so entao considerar pool multiprocessing se ainda necessario |
| GPU | Condicional: so se P1 + P2 confirmadas E medicao comparativa (P5) mostrar ganho real para os lotes do AXON. Nao assumir ganho a priori. |
| CoreML (Mac) | Mesmo padrao: passar `providers=['CoreMLExecutionProvider','CPUExecutionProvider']` via kwarg so se fastembed expoe o kwarg (P2) e medicao mostrar beneficio |
| Thread tuning | `SessionOptions.intra_op_num_threads = os.cpu_count()` passado via `providers_options` ou via variavel de ambiente `OMP_NUM_THREADS`. So apos P4 confirmar subutilizacao. |
| Pool de multiprocessing hand-rolled | YAGNI: nao construir. fastembed/onnxruntime ja sao multi-thread internamente. So justificavel se library-native nao atingir os alvos apos medicao. |
| Batching por comprimento (length-bucketed) | Agrupar chunks por faixa de token-count antes de passar para `embed()` para reduzir padding interno do onnxruntime. Teto de tokens por lote (token budget) para evitar pico de RSS. |
| Teto de tokens por lote | `MAX_BATCH_TOKENS = 8192` como default (ajustavel por env var `AXON_MAX_BATCH_TOKENS`). Se um unico chunk exceder o teto, ele vai em lote proprio (nao descartado). |
| Cap de chunk size | `_MAX_CHUNK_TOKENS = 512` tokens estimados por chunk. Chunks maiores sao divididos antes do embed. Impacta recall positivamente (chunks menores = busca mais precisa). Verificar recall guard antes e apos. |
| Reconcile por arquivo | Ao reindexar arquivo alterado: deletar todos os pontos Qdrant com `filter.must=[FieldCondition(key="file_path", match=MatchValue(value=str(path)))]` antes de upsert. Resolve a instabilidade de `start_line` (uuid5 muda quando linhas acima do simbolo sao editadas). |
| Exclusao de gitignored | Substituir `rglob` por `git ls-files --cached --others --exclude-standard` quando dentro de um repo git. Fallback para rglob se nao for repo git. Gitignored nao entra no Qdrant. |
| _FILE_HASH_CACHE persistente | Fora de escopo deste spec (coberto pelo Spec A - cache persistente em SQLite). Este spec nao toca o hash-cache. |

---

## Componentes e mudancas

### 1. `axon/embedder/engine.py` - provider auto-detectado

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

Thread tuning (condicional a P4 confirmar subutilizacao):
```python
# engine.py - aplicado antes de TextEmbedding() se P4 confirmada
import os
os.environ.setdefault("OMP_NUM_THREADS", str(os.cpu_count()))
```
Alternativa via `providers_options` se o kwarg estiver disponivel no fastembed.

Arquivo afetado: `C:/Users/samde/dev/axon/src/axon/embedder/engine.py`

### 2. `axon/embedder/pipeline.py` - batching com teto de tokens e reconcile por arquivo

**2a. Teto de tokens por lote (length-bucketed batching)**

Constantes novas (pipeline.py, apos linha 29):
```python
_MAX_BATCH_TOKENS: int = int(os.environ.get("AXON_MAX_BATCH_TOKENS", "8192"))
_MAX_CHUNK_TOKENS: int = int(os.environ.get("AXON_MAX_CHUNK_TOKENS", "512"))
_TOKENS_PER_CHAR: float = 0.25  # estimativa conservadora; 1 token ~= 4 chars
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
    Chunks maiores que _MAX_CHUNK_TOKENS sao truncados antes do embed
    (ver _truncate_chunk). Nunca descarta um chunk; se um chunk sozinho
    exceder o teto, vai em lote proprio."""
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
concatenar resultados. O `_BATCH_SIZE=400` existente continua controlando o flush pro
Qdrant (independente do batching de embedding).

**2b. Reconcile por arquivo (delete-antes-de-upsert)**

Apos confirmacao do gate "stale points" (premissa A10 do ledger), adicionar em
`index_path` antes do upsert de um arquivo:
```python
# pipeline.py - dentro do loop de arquivos, antes de pending_batch.extend()
await store.delete_by_file_path(str(file_path))
```

Novo metodo `VectorStore.delete_by_file_path(file_path: str)` em `vector_store.py`:
```python
async def delete_by_file_path(self, file_path: str) -> None:
    """Remove todos os pontos Qdrant com payload.file_path == file_path."""
    from qdrant_client.http.models import Filter, FieldCondition, MatchValue
    for ctx in self._collections():
        await self._client.delete(
            collection_name=ctx,
            points_selector=Filter(
                must=[FieldCondition(key="file_path",
                                    match=MatchValue(value=file_path))]
            ),
        )
```

Arquivo afetado: `C:/Users/samde/dev/axon/src/axon/embedder/pipeline.py`
Arquivo afetado: `C:/Users/samde/dev/axon/src/axon/store/vector_store.py`

### 3. `iter_supported_files` - git ls-files quando disponivel

Substituir `rglob` (pipeline.py:70) por chamada ao `git ls-files` quando dentro de repo
git. Fallback para rglob se nao for repo git (mesmo comportamento atual).

```python
# pipeline.py - nova implementacao de iter_supported_files
import subprocess

def _git_ls_files(target: Path) -> list[Path] | None:
    """Retorna lista de arquivos via git ls-files, ou None se nao for repo git."""
    try:
        result = subprocess.run(
            ["git", "-C", str(target), "ls-files",
             "--cached", "--others", "--exclude-standard"],
            capture_output=True, text=True, check=True
        )
        return [target / line for line in result.stdout.splitlines() if line]
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None

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
```

`_rglob_fallback` encapsula o `target.rglob("*")` atual, sem mudanca de logica.

Arquivo afetado: `C:/Users/samde/dev/axon/src/axon/embedder/pipeline.py`

### 4. Cap de chunk size no chunker (qualidade + memoria)

**Condicional a medicao de "large_chunks_found" no Phase 0.**

Se `large_chunks_found > 0` no baseline, adicionar cap em `chunk_source` para Python e
TypeScript (que hoje nao tem `_MAX_CHUNK_LINES`, ao contrario do Java que tem 80 linhas
em chunker.py:37):

- `_MAX_CHUNK_LINES_PY = 80` (mesma constante do Java, chunker.py:37)
- Reutilizar `_split_large_node()` (chunker.py:217-241) como template para Python/TypeScript

**Esta mudanca requer execucao completa da recall guard antes e apos** (ver secao abaixo).

Arquivo afetado: `C:/Users/samde/dev/axon/src/axon/embedder/chunker.py`

---

## Fluxo de dados (depois)

```
axon index <repo>
  |
  +-- iter_supported_files(repo)
  |     git ls-files (se repo git) OU rglob (fallback)
  |     exclui EXCLUDED_DIR_NAMES + gitignored automaticamente
  |
  +-- para cada arquivo alterado (hash-cache):
  |     chunk_source(source, language, path)
  |       → chunks com cap de _MAX_CHUNK_LINES por linguagem
  |     delete_by_file_path(path)  ← reconcile: limpa pontos orfaos
  |     _make_token_bounded_batches(chunks)
  |       → sub-lotes de <= _MAX_BATCH_TOKENS
  |     para cada sub-lote:
  |       engine.embed(texts)
  |         TextEmbedding(providers=[CUDA|CoreML|CPU])  ← auto-detectado
  |         OMP_NUM_THREADS = cpu_count()               ← se P4 confirmada
  |     pending_batch.extend(vector_chunks)
  |     se len(pending_batch) >= _BATCH_SIZE: flush → Qdrant
  |
  +-- flush final → Qdrant
  +-- build_dependency_records(graph_chunks) → Redis
```

---

## Guarda de Qualidade (Recall Guard)

A guarda de recall e um conjunto fixo de 20 pares `(query, expected_file, expected_symbol,
min_score)` armazenados em `tests/recall/golden_set.json`. Este arquivo e construido UMA
VEZ antes de qualquer mudanca de chunker ou embedder, a partir de uso real dos 9 repos.

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
| `_estimate_tokens()` | `pipeline.py` | Estimativa de tokens por chunk | nenhuma |
| `_make_token_bounded_batches()` | `pipeline.py` | Agrupa chunks sem exceder teto de tokens | nenhuma |
| `_git_ls_files()` | `pipeline.py` | Lista arquivos via `git ls-files` | subprocess mockavel |
| `iter_supported_files()` | `pipeline.py:59-75` | Walker com fallback rglob | `_git_ls_files` mockavel |
| `VectorStore.delete_by_file_path()` | `vector_store.py` | Deleta pontos por file_path | Qdrant client mockavel |
| `RecallBenchmarkFixture` | `axon/benchmark/recall.py` (novo) | Executa queries no golden set | `VectorStore`, `EmbedderEngine` injetaveis |

---

## Verificacao end-to-end

1. **Provider detection**: apos swap do wheel (se P1 confirmada), `python -c
   "from axon.embedder.engine import _detect_providers; print(_detect_providers())"` deve
   imprimir `['CUDAExecutionProvider', 'CPUExecutionProvider']` no desktop.

2. **Throughput com provider correto**: corpus sintetico de 500 funcoes; throughput >= 300
   chunks/s no desktop (ou >= 200 no M1 Pro). Se GPU nao ajudar (P5), alvo e cumprido via
   thread tuning apenas.

3. **RSS abaixo do teto**: `axon index <vault_root>` com psutil sampling; peak RSS <= 2 GB
   no desktop. Se P3 confirmada e streaming implementado, a lista `graph_chunks` nunca
   acumula mais de um arquivo em memoria.

4. **Reconcile funciona**: indexar arquivo, editar 3 linhas acima do primeiro simbolo,
   re-indexar; `vector_store.scroll(filter=file_path)` deve retornar exatamente o novo
   numero de chunks, sem pontos orfaos.

5. **Gitignored nao entra**: repo com `.env` em .gitignore; apos indexar, scroll Qdrant
   com filtro `file_path contains ".env"` deve retornar zero resultados.

6. **Recall guard passa**: `pytest tests/recall/test_recall_guard.py` com 0 regressoes;
   `current.score >= 0.90`.

7. **Wall time full index**: mediana de 3 runs com hash-cache frio; <= 5 min no desktop,
   <= 8 min no M1 Pro.

8. **Fallback sem GPU**: se `CUDAExecutionProvider` indisponivel, `_detect_providers()`
   retorna `['CPUExecutionProvider']` e `engine.embed()` funciona normalmente (sem erro).

---

## Testes

### Unit tests (sem model load, sem Qdrant)

- `test_detect_providers_cuda`: mocka `ort.get_available_providers()` retornando
  `['CUDAExecutionProvider', 'CPUExecutionProvider']`; assert retorno inclui CUDA primeiro.
- `test_detect_providers_cpu_fallback`: mocka disponivel = `['CPUExecutionProvider']`;
  assert retorno = `['CPUExecutionProvider']`.
- `test_detect_providers_coreml_mac`: mocka `platform.system()='Darwin'`,
  `platform.machine()='arm64'`, disponivel inclui `CoreMLExecutionProvider`; assert
  retorno = `['CoreMLExecutionProvider', 'CPUExecutionProvider']`.
- `test_make_token_bounded_batches_teto`: 10 chunks de 1000 tokens cada com teto 8192;
  verifica que batches resultantes nao excedem teto e soma de chunks = 10.
- `test_make_token_bounded_batches_chunk_gigante`: 1 chunk com 20.000 tokens; vai em lote
  proprio (nao descartado).
- `test_git_ls_files_ok`: subprocess mockado retornando lista de arquivos; verifica paths.
- `test_git_ls_files_fallback`: `CalledProcessError` levantado; `iter_supported_files`
  usa rglob.
- `test_delete_by_file_path_chama_qdrant`: Qdrant client mockado; verifica que
  `delete_by_file_path` chama `client.delete` com o filtro correto por `file_path`.

### Integration tests (Qdrant via testcontainers, sem model load - embedder mockado)

- `test_reconcile_orfaos`: indexar arquivo (3 chunks mockados), editar start_line (+3),
  re-indexar; scroll deve retornar 3 pontos, nao 6.
- `test_gitignored_excluido`: repo com `.env` em .gitignore; apos `index_path`, scroll
  Qdrant nao deve retornar nenhum ponto com `file_path` contendo `.env`.
- `test_idempotencia_provider_fallback`: `_ensure_model` e chamado duas vezes; `TextEmbedding`
  e instanciada so uma vez (lazy init correto).

### Recall guard (real embedding + Qdrant container)

- `tests/recall/test_recall_guard.py::test_no_regression` - ver secao "Guarda de Qualidade".
- `tests/recall/test_recall_guard.py::test_top1_gte_090` - score Top-1 >= 0.90.
- `tests/recall/test_recall_guard.py::test_top3_gte_095` - score Top-3 >= 0.95.

Cobertura alvo: 80%+ nas unidades novas e alteradas.

---

## Fora de escopo

- Cache de hash persistente entre processos (SQLite) - coberto pelo Spec A.
- Pipeline Redis para `upsert_deps` sequencial - coberto pelo Spec C.
- Re-parse duplo de AST em `graph_extractor.py` - coberto pelo Spec C.
- Mudanca no conjunto de linguagens suportadas (`_LANGUAGE_MAP`).
- Pool de multiprocessing hand-rolled para embedding - YAGNI ate medicao provar necessidade.
- Upgrade de modelo (bge-large, bge-m3, etc.) - mudaria dimensoes e invalidaria colecoes existentes.
- Markdown/text chunking com tree-sitter - fora deste overhaul de performance.
- Locking distribuido entre hook e index manual concorrentes - identificado como risco mas tratado no Spec A (hash-cache) ou como trabalho futuro com asyncio.Lock.
