# Design: Perf Overhaul A - Teto universal de chunks, parse unico e file-walk via git ls-files

Data: 2026-06-19
Status: rascunho (aguardando gate Phase 0)
Escopo: tres mudancas ortogonais no caminho de indexacao (chunker + pipeline + file-walk) que
formam o pilar **linear** do overhaul de performance. Nao inclui paralelismo nem GPU.

Este e o Spec A de tres (A = linear, B = cacheable, C = parallel). Os tres compartilham os
mesmos criterios de sucesso numericos e o mesmo recall-guard de 20 queries.

---

## Contexto

### Tres problemas identificados em codigo

**Problema 1 - Ausencia de teto de chunk em Python/TypeScript/Markdown**

`chunker.py:37` define `_MAX_CHUNK_LINES = 80`. Java aplica esse teto tanto em metodos
(`chunker.py:170-173`) quanto em classes sem metodos (`chunker.py:142-145`) e em
records/enums/annotations (`chunker.py:108-109`). A funcao `_split_large_node`
(`chunker.py:217-241`) implementa o split por stride de 80 linhas.

Python e TypeScript **nao aplicam esse teto**. `_walk_python` (`chunker.py:326-338`) emite
um chunk por `function_definition` independente do tamanho. `_walk_ts` (`chunker.py:448-479`)
faz o mesmo para `function_declaration`, `method_definition` e arrow functions. Markdown e
texto caem no catchall de `chunk_source` (`chunker.py:640-650`) e viram **um unico chunk**
independente do tamanho do arquivo.

Consequencia: uma funcao Python de 1 000 linhas gera um chunk de 1 000 linhas, que ocupa
muito mais do orçamento de tokens do modelo do que o teto de 80 linhas permitiria, degrada
a qualidade de retrieval (menos diversidade por query) e causa o pico de RSS de ~14 GB
observado quando `graph_chunks: list[Chunk]` (`pipeline.py:141`) acumula esses chunks
gigantes em memoria.

**Problema 2 - Duplo parse na extracao de call-edges**

`pipeline.py:196-197` chama `build_dependency_records(graph_chunks)`. Dentro de
`graph_extractor.py:57-76`, para cada chunk, `extract_calls(chunk)` re-parseia o campo
`chunk.content` do zero: Python via `ast.parse` (`graph_extractor.py:94`) e
Java/TypeScript via `parser.parse(source.encode())` (`graph_extractor.py:128`). O chunker
ja construiu essas arvores em `_chunk_python` (`chunker.py:300`) e `_chunk_typescript`
(`chunker.py:429`) mas descartou o `Node` raiz apos construir os chunks.

Resultado: cada arquivo e parseado duas vezes - uma em `chunker.py` para extrair simbolos,
outra em `graph_extractor.py` para extrair call-edges a partir do campo `.content` (que e
somente o texto do chunk, nao o arquivo inteiro). O segundo parse e sobre um fragmento, nao
o arquivo completo, o que tambem limita a exatidao dos call-edges.

**Problema 3 - rglob sem poda de diretorios**

`pipeline.py:70` usa `target.rglob("*")` e entao filtra cada caminho individualmente por
`path.parts` (`pipeline.py:71`). Em repos com `.venv`, `node_modules` ou `.git` grandes, o
rglob percorre todos os inodes desses diretorios antes de descartar. O indexer ja tem a
solucao correta: `indexer.py:78-89` usa `git ls-files --cached --others --exclude-standard`
e cai de volta para `iter_supported_files` apenas fora de git repos.

Consequencia adicional de seguranca: o rglob pode incluir arquivos que o `.gitignore` exclui
(`.env`, `secrets.json`, chaves privadas). Se esses arquivos tiverem extensao `.py` ou `.ts`
eles sao embeddados no Qdrant. Isso e uma **vulnerabilidade de privacidade/seguranca**.

Fatos verificados no codigo:
- `chunker.py:37` (`_MAX_CHUNK_LINES = 80`), aplicado Java em `:170-173`, `:108-109`, `:142-145`
- `chunker.py:217-241` (`_split_large_node`, template reutilizavel)
- `chunker.py:300` (`_PY_PARSER.parse` descartado apos chunking)
- `chunker.py:326-338` (Python: sem teto de tamanho)
- `chunker.py:448-479` (TypeScript: sem teto de tamanho)
- `chunker.py:613-651` (`chunk_source` dispatcher; catchall linha 640-650)
- `chunker.py:640-650` (markdown/text: arquivo inteiro como 1 chunk)
- `graph_extractor.py:57-76` (`build_dependency_records` recebe chunks ja prontos)
- `graph_extractor.py:79-89` (`extract_calls` por chunk)
- `graph_extractor.py:94` (`ast.parse` segundo parse Python)
- `graph_extractor.py:128` (tree-sitter segundo parse Java/TS)
- `pipeline.py:59-75` (`iter_supported_files` com `rglob('*')`)
- `pipeline.py:141` (`graph_chunks: list[Chunk]` acumula em memoria ate o fim)
- `pipeline.py:196-202` (loop sequencial `build_dependency_records` + `upsert_deps`)
- `indexer.py:78-89` (`git ls-files` ja implementado, com fallback)

---

## Decisoes

| Tema | Decisao |
|---|---|
| Teto de chunk | `_MAX_CHUNK_LINES = 80` aplicado a **todas** as linguagens: Python, TypeScript, Markdown, texto. Reutiliza `_split_large_node` existente. |
| Markdown por secao | `.md` e tokenizado por cabecalho (`# / ## / ###`); cada secao vira um chunk; se a secao ultrapassar 80 linhas, aplica `_split_large_node`. |
| Parse unico | O chunker devolve `(chunks, tree)` ou armazena a arvore em `Chunk.metadata["_tree"]`; `graph_extractor` reutiliza a arvore em vez de re-parsear. Arvore descartada apos extracao de call-edges (nao persiste em Qdrant). |
| File-walk | `iter_supported_files` substituido por wrapper de `git ls-files --cached --others --exclude-standard`; fallback para rglob somente fora de git repos. |
| Seguranca gitignore | Arquivos listados apenas pelo rglob (nao pelo `git ls-files`) **nunca** sao embeddados. Requisito verificavel por teste automatizado. |
| Compatibilidade de IDs | `_chunk_id = uuid5(path::symbol::start_line)` (`pipeline.py:206-211`) nao muda de formula, mas ao aplicar o teto de 80 linhas em Python/TS, chunks existentes tem novos `start_line` (o split gera subfaixas). Exige reindex completo dos 9 repos antes de ativar. |
| Reconciliacao por arquivo | Ao re-indexar um arquivo modificado: delete todos os pontos Qdrant com `file_path == <path>`, depois upsert os novos. Nunca diff por chunk-id (instavel por `start_line`). |
| Ordem de entrega | (1) teto de chunks + markdown, (2) parse unico, (3) git ls-files. Cada item e um PR independente com benchmark antes/depois e recall-guard passando. |
| YAGNI | Paralelismo e GPU nao entram neste spec. Sao candidatos para Spec C e Spec B respectivamente, dependentes do gate Phase 0. |

---

## Componentes e mudancas

### 1. Teto universal de chunk em `chunker.py`

**1a. Python** - adicionar verificacao de tamanho em `_walk_python` apos emitir o chunk:

```python
# chunker.py - dentro de _walk_python, apos o append do chunk (linha ~338)
if (node.end_point[0] - node.start_point[0] + 1) > _MAX_CHUNK_LINES:
    # substitui o chunk recem-appendado por sub-chunks
    chunks.pop()
    chunks.extend(_split_large_node_from_lines(
        lines, node.start_point[0], node.end_point[0],
        symbol or Path(file_path).stem,
        "method" if in_class else "function",
        file_path, "python"
    ))
    return  # nao recursar para filhos: o split ja cobre o conteudo
```

Alternativa preferida por clareza: checar o tamanho antes de appendar e chamar
`_split_large_node` quando exceder o teto, sem modificar a lista em retrospecto.

**1b. TypeScript** - mesma logica em `_ts_chunk_from_node` (`chunker.py:495-512`): se
`end - start + 1 > _MAX_CHUNK_LINES`, retornar lista de sub-chunks em vez de um Chunk unico.
Isso exige mudar a assinatura de `_ts_chunk_from_node` para `list[Chunk]` e adaptar
`_walk_ts` para `chunks.extend(...)` em vez de `chunks.append(...)`.

**1c. `_split_large_node` generalizado** - a versao atual (`chunker.py:217-241`) recebe `Node`
tree-sitter. Precisamos de uma variante que aceite apenas linhas de texto (para Python via
tree-sitter e para Markdown):

```python
def _split_lines_into_chunks(
    lines: list[str],
    start_line_1based: int,
    symbol: str,
    chunk_type: ChunkType,
    file_path: str,
    language: str,
) -> list[Chunk]:
    """Divide uma lista de linhas em sub-chunks de _MAX_CHUNK_LINES cada."""
    result = []
    for i in range(0, len(lines), _MAX_CHUNK_LINES):
        part = lines[i : i + _MAX_CHUNK_LINES]
        idx = i // _MAX_CHUNK_LINES
        result.append(Chunk(
            symbol=f"{symbol}[{idx}]" if idx > 0 else symbol,
            chunk_type=chunk_type,
            start_line=start_line_1based + i,
            end_line=start_line_1based + i + len(part) - 1,
            content="\n".join(part),
            file_path=file_path,
            language=language,
        ))
    return result
```

**1d. Markdown por secao** - novo branch em `chunk_source` (`chunker.py:613-651`) para
`language == "markdown"`:

```python
elif language == "markdown":
    return _chunk_markdown(source, file_path)
```

Logica de `_chunk_markdown`:
- Divide o arquivo por linhas.
- Detecta cabecalhos: linha comecando com `#` (ate `######`).
- Cada secao (cabecalho ate o proximo cabecalho de nivel igual ou superior) vira um chunk com
  `symbol = <texto do cabecalho normalizado>`, `chunk_type = "class"` (reuso do tipo
  existente), `language = "markdown"`.
- Conteudo antes do primeiro cabecalho vira um chunk com `symbol = Path(file_path).stem`.
- Se a secao ultrapassar `_MAX_CHUNK_LINES`, aplica `_split_lines_into_chunks`.
- Arquivo sem nenhum cabecalho cai no comportamento atual (arquivo inteiro como 1 chunk),
  mas agora com o teto aplicado.

**1e. Texto (`.txt`)** - aplica `_split_lines_into_chunks` diretamente no catchall quando
`language == "text"`, usando `symbol = Path(file_path).stem` e indice como sufixo.

### 2. Parse unico em `graph_extractor.py`

A abordagem menos invasiva e armazenar a arvore tree-sitter em `Chunk.metadata["_tree"]`
durante o chunking e consumi-la em `extract_calls`. A arvore nao e serializada (nao chega ao
Qdrant - o campo `metadata` do `VectorChunk` e construido explicitamente em
`pipeline.py:171-184`).

**2a. Mudanca em `chunker.py`** - em `_chunk_python` (`chunker.py:300`) e `_chunk_typescript`
(`chunker.py:429`), apos `tree = parser.parse(...)`, propagar a arvore para cada Chunk via
`metadata={"_tree": tree}`. Para chunks derivados de `_split_lines_into_chunks`, a arvore e
a mesma do arquivo pai (o split e sobre o texto, nao sobre a arvore).

Para o parser Java (`chunker.py:619-621`), a arvore ja e construida e descartada. Inclui-la
em `metadata={"_tree": tree}` no `Chunk` retornado.

**2b. Mudanca em `graph_extractor.py`** - em `extract_calls` (`graph_extractor.py:79-89`),
verificar `chunk.metadata.get("_tree")` antes de re-parsear:

```python
def extract_calls(chunk: Chunk) -> list[str]:
    cached_tree = chunk.metadata.get("_tree")
    if chunk.language == "python":
        if cached_tree is not None:
            calls = _extract_python_calls_from_ast(cached_tree)
        else:
            calls = _extract_python_calls(chunk.content)  # fallback
    elif chunk.language in ("java",):
        if cached_tree is not None:
            calls = _extract_calls_from_ts_tree(cached_tree)
        else:
            calls = _extract_ts_or_java_calls(chunk.content, _JAVA_CALL_PARSER)
    elif chunk.language in {"typescript", "ts"}:
        parser = _TSX_PARSER if chunk.file_path.endswith(".tsx") else _TS_PARSER
        if cached_tree is not None:
            calls = _extract_calls_from_ts_tree(cached_tree)
        else:
            calls = _extract_ts_or_java_calls(chunk.content, parser)
    else:
        calls = []
    return sorted(call for call in calls if call != chunk.symbol)
```

`_extract_python_calls_from_ast` aceita um `ast.Module` (resultado de `ast.parse`) ou um
objeto de arvore tree-sitter, dependendo de como a arvore foi armazenada. Para Python,
`chunker.py:300` usa tree-sitter (nao `ast`); logo a arvore armazenada e um `tree_sitter.Tree`.
`_extract_python_calls` atual (`graph_extractor.py:92-105`) usa `ast.parse` - portanto a
integracao exige ou: (a) mudar `_chunk_python` para usar `ast` internamente (regressao
potencial para Python 3.12+ syntax), ou (b) adicionar `_walk_calls_ts_tree` que funcione
sobre a arvore tree-sitter do Python. **Opcao b e preferida** por manter a cobertura de
sintaxe moderna.

**2c. Limpeza de memoria** - apos `build_dependency_records(graph_chunks)` retornar
(`pipeline.py:197`), limpar `chunk.metadata["_tree"]` de cada chunk em `graph_chunks` antes
do garbage collector agir. A arvore tree-sitter ocupa memoria proporcional ao arquivo; para
repos grandes esse detalhe reduz o pico de RSS.

### 3. File-walk via `git ls-files` em `pipeline.py`

**3a. Extrair utilidade compartilhada** - a logica de `indexer.py:71-89` (`_iter_repo_files`)
e mover para `axon/repo/file_walk.py` como funcao publica `iter_git_files(root: Path, ...) -> list[Path]`:

```python
# axon/repo/file_walk.py
def iter_git_files(
    root: Path,
    *,
    suffixes: set[str],
) -> list[Path]:
    """Lista arquivos rastreados/untracked respeitando .gitignore.

    Usa `git ls-files --cached --others --exclude-standard` quando root e
    um git repo. Fallback para rglob somente quando git nao esta disponivel
    ou root nao e um repo.

    REQUISITO DE SEGURANCA: arquivos gitignored nunca sao retornados por
    este caminho. O fallback rglob tambem aplica `EXCLUDED_DIR_NAMES` mas
    nao garante exclusao de gitignored; documentar essa limitacao no fallback.
    """
```

**3b. Modificar `iter_supported_files` em `pipeline.py:59-75`** - substituir o corpo por
uma chamada a `iter_git_files` quando `target` for um diretorio (mantendo o comportamento
de arquivo unico inalterado para `target.is_file()`):

```python
def iter_supported_files(target: Path, *, languages: set[str] | None = None) -> Iterable[Path]:
    if target.is_file():
        language = _language_for_suffix(target.suffix)
        if language and (languages is None or language in languages):
            yield target
        return

    suffixes = {s for s, lang in _LANGUAGE_MAP.items()
                if languages is None or lang in languages}
    yield from iter_git_files(target, suffixes=suffixes)
```

**3c. Preservar fallback** - `iter_git_files` cai para `rglob` quando `git` nao esta
disponivel (mesmo comportamento atual), mas documenta que o fallback nao garante exclusao
de gitignored files. Fora de git repos a garantia de seguranca nao se aplica.

**3d. Atualizar `indexer.py:71-89`** - remover a implementacao local de `_iter_repo_files`
e importar `iter_git_files` de `axon.repo.file_walk`. Comportamento identico ao atual.

---

## Fluxo de dados (depois)

```
iter_git_files(root)          # git ls-files, sem rglob sobre .venv/.git
  -> [file_path, ...]
    para cada arquivo:
      chunk_source(source, lang, path)
        _walk_python / _walk_ts / _chunk_markdown
          -> chunks com metadata["_tree"] = arvore parseada
      embed(chunks)
      upsert_batch(vector_chunks)   # sem _tree no payload Qdrant
      graph_chunks.extend(chunks)
  build_dependency_records(graph_chunks)
    extract_calls(chunk)
      usa chunk.metadata["_tree"]   # sem segundo parse
    upsert_deps(...)
  limpa metadata["_tree"] dos chunks
```

Nota: `vector_chunks` em `pipeline.py:171-184` constroem `VectorChunk` com campos explicitados;
`metadata` do `Chunk` (que contem `_tree`) **nao e copiado** para o payload Qdrant. Isso e
seguro sem mudanca adicional - confirmar na implementacao que nenhum `**chunk.metadata` e
passado para `VectorChunk`.

---

## Reconciliacao por arquivo (corretude de IDs)

`_chunk_id` (`pipeline.py:206-211`) usa `uuid5(path::symbol::start_line)`. Quando o teto de
80 linhas e aplicado a Python/TS, o split de um metodo grande gera sub-chunks com
`symbol = "foo[0]"`, `"foo[1]"` etc. Editar 3 linhas acima do metodo desloca os `start_line`
de todos os chunks subsequentes, gerando novos IDs para chunks cujo conteudo nao mudou.

**Estrategia: reconciliacao per-file.** Antes de upsert, deletar todos os pontos Qdrant
com `file_path == str(file_path.resolve())` para o arquivo reindexado:

```python
# pipeline.py - dentro do loop de index_path, antes de pending_batch.extend
if graph_store is not None or True:  # sempre deletar antes de upsert
    await store.delete_by_file(str(file_path.resolve()))
```

`VectorStore.delete_by_file(file_path: str)` usa o filtro Qdrant `MatchValue` sobre o campo
`file_path` do payload. Esse metodo ainda nao existe em `vector_store.py`; e um dos itens
deste spec.

Alternativa: usar `store.upsert_batch` com `upsert` semantico do Qdrant (que substitui
pontos de mesmo ID). Isso nao remove pontos orfaos de chunks que deixaram de existir apos
reducao do arquivo. Por isso o delete-before-upsert e obrigatorio.

---

## Criterios de sucesso (numericos, por maquina)

| Metrica | R7 5800X3D (desktop) | M1 Pro (mac) | Como medir |
|---|---|---|---|
| Wall time index completo (9 repos, hash-cache frio) | <= 5 min | <= 8 min | `time axon index <vault>`, 3 runs, mediana |
| Wall time refresh incremental (1 arquivo, 10-50 chunks) | <= 10 s | <= 15 s | 5 arquivos de tamanhos variados, todos devem passar |
| Wall time hook post-commit (20 arquivos) | <= 30 s | <= 45 s | 3 runs, tomar o maximo |
| Pico de RSS durante index completo | <= 2 GB | <= 1.5 GB | psutil sampling a cada 2 s |
| Throughput (chunks/s end-to-end) | >= 300 | >= 200 | corpus sintetico fixo de 500 funcoes Python 15-30 linhas |
| Recall Top-1 (20 queries golden set) | >= 0.90 | >= 0.90 | harness recall-guard (ver secao abaixo) |
| Recall Top-3 (20 queries golden set) | >= 0.95 | >= 0.95 | mesmo harness |
| Arquivos gitignored embeddados | 0 pontos Qdrant | 0 pontos Qdrant | scroll Qdrant pos-index, checar `.env` e `secrets.json` |
| Pontos orfaos apos edicao de 3 linhas acima de um simbolo | 0 pontos orfaos | 0 pontos orfaos | scroll por file_path antes e depois da edicao |

**Gate de Phase 0 (obrigatorio antes de qualquer PR deste spec):**

1. `benchmarks/phase0_baseline.json` commitado com todos os campos preenchidos.
2. `recall_top1_baseline >= 0.80` e `recall_top3_baseline >= 0.80` no baseline pre-overhaul.
3. Pelo menos um gargalo confirmado com numero medido (nao estimativa) e ranqueado por
   contribuicao ao wall time total.
4. `gpu_available` registrado; se `false`, GPU e removida do plano Spec C completamente.
5. `stale_qdrant_points_confirmed` registrado; se `true`, delete-before-upsert e item 0 do PR.

---

## Recall-guard (qualidade semantica)

Antes de qualquer mudanca em chunker, embedder ou pipeline:

1. **Golden set** - 20 triplas `(query, expected_file_path, expected_symbol)` armazenadas em
   `tests/recall/golden_set.json`. Distribuicao: 8 queries Python, 5 Java, 4 TypeScript, 3
   cross-file. Arquivo imutavel por codigo; so atualizado por decisao humana explicita com
   commit separado.

2. **Harness** - `RecallBenchmarkFixture` espelha o shape de `RetrievalBenchmarkFixture`
   (`src/axon/benchmark/retrieval.py`). Usa Qdrant real via `testcontainers[qdrant]` (ja nos
   dev extras de `pyproject.toml`). Indexa o corpus de referencia (`src/axon/embedder/`,
   `src/axon/store/`) em container fresh antes de rodar as queries.

3. **Checks por query** (reusa `BenchmarkCheck` de `src/axon/benchmark/contracts.py`):
   - `top_1_file_match`: `hits[0].payload["file_path"] == expected_file`
   - `top_3_file_match`: `expected_file` em `{hits[0..2].payload["file_path"]}`
   - `min_score`: `hits[0].score >= min_score` (minimo 0.70)
   - `symbol_match`: `hits[0].payload["symbol"] == expected_symbol`

4. **Gate de regressao** - pytest carrega `tests/recall/baseline.json` e asserta
   `len(report.regressions) == 0` usando `compare_benchmark_runs` de `reporting.py`.
   `BenchmarkRunSummary.score` nao pode cair abaixo de 0.90.

5. **Embedding em subprocess** - se a hipotese de que o pico de 14 GB vem de acumulacao em
   `graph_chunks` for refutada e o leak for de carga de modelo, o harness deve rodar o
   embedding em subprocess separado (coleta resultado via stdout JSON) para nao contaminar o
   processo de benchmark.

---

## Teste de seguranca: gitignored files nunca embeddados

**Fixture** (`tests/test_file_walk_security.py`):

```python
@pytest.mark.asyncio
async def test_gitignored_files_never_embedded(tmp_path):
    # Arrange: repo git com .env e secrets.json no .gitignore
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True)
    (repo / ".gitignore").write_text(".env\nsecrets.json\n")
    (repo / ".env").write_text("SECRET_KEY=abc123\n")
    (repo / "secrets.json").write_text('{"password": "hunter2"}\n')
    (repo / "main.py").write_text("def hello(): pass\n")
    subprocess.run(["git", "-C", str(repo), "add", ".gitignore", "main.py"], check=True)

    # Act: listar arquivos via iter_git_files
    from axon.repo.file_walk import iter_git_files
    files = iter_git_files(repo, suffixes={".py", ".env", ".json"})

    # Assert: .env e secrets.json ausentes; main.py presente
    paths = {f.name for f in files}
    assert ".env" not in paths
    assert "secrets.json" not in paths
    assert "main.py" in paths
```

Este teste e um **gate de seguranca**: falhar e bloqueante para merge, igual a CRITICAL no
checklist de code-review. Deve rodar em CI sem dependencias externas (somente `git` no PATH).

---

## Unidades (isolamento)

| Unidade | Arquivo | Dependencias | Testavel isolado? |
|---|---|---|---|
| `_split_lines_into_chunks` | `chunker.py` | nenhuma (funcao pura) | sim - entrada: linhas, saida: lista de Chunk |
| `_chunk_markdown` | `chunker.py` | `_split_lines_into_chunks` | sim - entrada: string markdown |
| Teto em `_walk_python` | `chunker.py` | `_split_lines_into_chunks` | sim - funcao Python >80 linhas deve gerar N chunks |
| Teto em `_walk_ts` / `_ts_chunk_from_node` | `chunker.py` | `_split_lines_into_chunks` | sim |
| `extract_calls` com cache de arvore | `graph_extractor.py` | `Chunk.metadata["_tree"]` | sim - mock de chunk com arvore pre-parseada |
| `iter_git_files` | `axon/repo/file_walk.py` | subprocess `git` | sim com tmp_path + git init |
| `iter_supported_files` atualizado | `pipeline.py` | `iter_git_files` | sim - injetar mock de `iter_git_files` |
| `VectorStore.delete_by_file` | `vector_store.py` | Qdrant client | sim com testcontainers[qdrant] |
| Reconciliacao per-file em `index_path` | `pipeline.py` | `store.delete_by_file` | sim - verificar contagem de pontos antes/depois |

---

## Verificacao end-to-end

1. **Teto de chunks**: indexar arquivo Python com funcao de 200 linhas; `chunk_source`
   deve retornar 3 chunks (linhas 1-80, 81-160, 161-200). Verificar `start_line` e
   `end_line` de cada chunk.

2. **Markdown por secao**: indexar `README.md` com 3 cabecalhos; verificar que
   `chunk_source` retorna 3+ chunks (um por secao), nenhum com mais de 80 linhas.

3. **Arquivo markdown gigante sem cabecalho**: indexar `.md` de 500 linhas sem `#`;
   verificar que `chunk_source` retorna 7 chunks de 80 linhas (6) + 1 de 20 linhas.

4. **Parse unico**: instrumentar `ast.parse` e `parser.parse` em `graph_extractor.py`
   com um contador; indexar 10 arquivos Python; contador deve ser 0 (todas as chamadas
   usaram a arvore cacheada).

5. **git ls-files**: indexar repo com `.env` no `.gitignore`; scroll Qdrant e verificar
   ausencia de pontos com `file_path` contendo `.env`.

6. **Reconciliacao per-file**: indexar arquivo com 2 funcoes; editar 3 linhas antes da
   primeira funcao (desloca start_line); re-indexar; `scroll(filter=file_path)` deve
   retornar exatamente 2 pontos (nao 4).

7. **Throughput**: corpus sintetico de 500 funcoes Python de 15-30 linhas; throughput
   deve ser >= 300 chunks/s no desktop. Registrar em `benchmarks/phase0_baseline.json`
   como denominador para calculos de ganho.

8. **Recall pos-overhaul**: rodar `compare_benchmark_runs(current, baseline)` e verificar
   `regressions == []` e `score >= 0.90`.

---

## Testes

### Unitarios (sem I/O externo)

- `test_split_lines_into_chunks`: entrada de 200 linhas -> 3 chunks; verifica `start_line`,
  `end_line`, `symbol` com sufixo `[0]`/`[1]`/`[2]`.
- `test_chunk_python_size_cap`: funcao Python de 100 linhas -> 2 chunks; funcao de 79 linhas
  -> 1 chunk (abaixo do teto).
- `test_chunk_typescript_size_cap`: mesmo padrao para `.ts`.
- `test_chunk_markdown_with_headers`: markdown com 3 cabecalhos -> 3+ chunks, nenhum > 80 linhas.
- `test_chunk_markdown_no_header_large`: 500 linhas sem cabecalho -> 7 chunks de <=80 linhas.
- `test_chunk_text_large`: `.txt` de 160 linhas -> 2 chunks de 80 linhas.
- `test_extract_calls_uses_cached_tree`: mock de `ast.parse` que levanta; chunk com
  `metadata["_tree"]` preenchido; `extract_calls` deve retornar sem chamar `ast.parse`.
- `test_chunk_source_dispatcher_markdown`: `chunk_source(source, "markdown", path)` nao
  deve retornar chunk com `end_line - start_line + 1 > 80`.

### Integracao (git + filesystem)

- `test_iter_git_files_excludes_gitignored` (= teste de seguranca descrito acima).
- `test_iter_git_files_fallback_no_git`: diretorio sem `.git`; `iter_git_files` deve usar
  rglob e retornar arquivos com os suffixes corretos.
- `test_delete_by_file_removes_stale_points`: com Qdrant via testcontainers, inserir 3
  pontos para `file_path=A`, chamar `delete_by_file(A)`, scroll retorna 0 pontos.
- `test_reconcile_per_file_no_orphans`: index arquivo (2 funcoes), editar, re-index;
  scroll por file_path retorna exatamente 2 pontos.

### Benchmarks / gate

- `test_recall_guard_no_regression`: carrega `tests/recall/baseline.json`, roda harness,
  asserta `regressions == []` e `score >= 0.90`.
- `test_gitignored_never_embedded_integration`: sobe Qdrant via testcontainers, indexa repo
  com `.env` no `.gitignore`, scroll all points, asserta 0 hits com `.env` no `file_path`.

Cobertura alvo: 80%+ nas unidades novas/alteradas (`chunker.py`, `graph_extractor.py`,
`axon/repo/file_walk.py`, `vector_store.py` metodo novo, `pipeline.py` loop alterado).

---

## Migração dos 9 repos ja indexados

Os repos `personal` / `knowledge` foram indexados com a versao anterior do chunker (sem teto
em Python/TS, sem markdown por secao). Apos este spec ser aplicado, os chunk-IDs mudam para
qualquer funcao Python/TS maior que 80 linhas, gerando pontos orfaos.

**Procedimento de migracao one-shot (pre-merge do primeiro PR deste spec):**

1. Registrar `benchmarks/phase0_baseline.json` (gate Phase 0 completo).
2. Fazer backup dos nomes de colecoes Qdrant existentes.
3. Rodar `axon index <vault_root>` com a versao nova (com delete-before-upsert ativo).
   O delete-before-upsert garante que pontos orfaos do chunking antigo sejam removidos
   durante o prorio re-index, sem necessidade de DROP de colecao.
4. Verificar recall-guard pos-migracao: score >= 0.90.
5. Verificar: contagem de pontos por colecao antes e depois. Se a contagem subir alem do
   esperado (o teto gera mais chunks para arquivos grandes), e normal e esperado.

---

## Fora de escopo

- Paralelismo de I/O ou CPU no pipeline (Spec C).
- GPU / troca de provider ONNX Runtime (Spec B, dependente do gate de GPU em Phase 0).
- Cache persistente de hashes entre processos (Spec B - cacheable).
- Pipelining de `upsert_deps` Redis (Spec B ou C).
- Novas linguagens (Go, Rust, Bash).
- Mudanca no modelo de embedding ou nas dimensoes dos vetores.
- Alteracao na estrutura do `VectorChunk` ou do schema Qdrant.
- Schema migration SQLite para este spec (nao ha nova tabela; `file_index` e escopo do Spec B
  se confirmado necessario pelo gate).

---

## Pressupostos a verificar (antes de implementar)

| Pressuposto | Verificacao barata |
|---|---|
| `rglob` e gargalo mensuravel no wall time de index | `time python -c "list(Path('<vault>').rglob('*'))"` vs `time git ls-files <vault> \| wc -l`; se rglob < 1 s, nao e gargalo e o beneficio do git ls-files e so seguranca |
| Funcoes Python/TS grandes existem nos 9 repos hoje | `python -c "from axon.embedder.chunker import chunk_source; ..."` contar chunks com end_line - start_line > 80 no repo maior |
| Arvore tree-sitter e serializavel em `dict` via `Chunk.metadata` | testar que `Chunk(metadata={"_tree": tree})` nao levanta em `Chunk.model_validate` (Pydantic v2 aceita `Any` em `dict`) |
| `ast.parse` no `graph_extractor` e chamado sobre chunk.content (fragmento), nao arquivo inteiro | confirmar em `graph_extractor.py:94` que `source = chunk.content`, nao `chunk.file_path.read_text()` |

---

## Notas de implementacao

- Nao usar `em-dash` ou `en-dash` em nenhum comentario de codigo ou docstring gerado.
- Preservar compatibilidade de assinatura de `chunk_source(source, language, file_path)` -
  a assinatura publica nao muda.
- `_split_lines_into_chunks` e privada (prefixo `_`); nao exportar no `__init__.py`.
- O campo `metadata` de `Chunk` e `dict = Field(default_factory=dict)` (`chunker.py:48`);
  Pydantic v2 aceita qualquer valor serializavel. A arvore tree-sitter nao e JSON-serializavel
  mas nao e persistida (so usada em memoria ate o fim de `index_path`).
- Ao limpar `metadata["_tree"]` apos `build_dependency_records`, usar `chunk.metadata.pop("_tree", None)`
  em vez de atribuicao direta para evitar KeyError se o chunk vier de linguagem sem arvore
  cacheada (markdown, texto).
