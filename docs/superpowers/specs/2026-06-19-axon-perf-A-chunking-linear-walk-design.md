# Design: Perf Overhaul A - Teto universal de chunks, parse unico e file-walk via git ls-files

Data: 2026-06-19
Status: rascunho (aguardando gate Phase 0)
Escopo: tres mudancas ortogonais no caminho de indexacao (chunker + pipeline + file-walk) que
formam o pilar **linear** do overhaul de performance. Nao inclui paralelismo nem GPU.

Este e o Spec A de tres (A = linear, B = cacheable, C = parallel). Os tres compartilham os
mesmos criterios de sucesso numericos e o mesmo recall-guard de 20 queries.

---

## Decisoes de design aplicadas neste spec (D1-D6)

As decisoes abaixo foram consolidadas apos revisao de codigo e substituem qualquer texto
conflitante que possa ter existido em rascunhos anteriores.

**D1 - Chunk-ID estavel via uuid5(NAMESPACE_URL, f"{file_path}::{symbol}::{occurrence_index}")**
`occurrence_index` e o indice 0-based do nome do simbolo dentro do arquivo (distingue
overloads e sub-chunks como `foo[0]` / `foo[1]`). `start_line` e removido da chave. Editar
linhas ACIMA de um simbolo nao altera seu ID - nenhum ponto Qdrant orfao e criado por deslocamentos de linha.

**D2 - Crash-safety via sentinela pending na tabela file_index**
A tabela `file_index` recebe coluna `status`. Re-indexar um arquivo: (a) gravar a linha com
`status='pending'` + novo sha ANTES de mutar o Qdrant; (b) `delete_by_file` seguido de upsert
dos novos pontos; (c) setar `status='done'`. Em qualquer run, uma linha `'pending'` e tratada
como suja e re-indexada. A migracao one-shot dos 9 repos ja indexados usa BLUE/GREEN: indexar
em nova colecao Qdrant, rodar o recall gate, promover (alias swap) apenas se passar; do
contrario, manter a colecao antiga. Runs incrementais normais NAO usam blue/green.

**D3 - Walk scope = somente tracked + check-ignore**
Usar `git ls-files --cached` (SEM `--others`), depois filtrar cada caminho por
`git check-ignore` para que arquivos que foram comitados e depois adicionados ao `.gitignore`
tambem sejam excluidos. Arquivos nao-rastreados exigem `git add` antes de serem indexados.
Garantia de seguranca: nenhum arquivo gitignored e embeddado. Adicionar um teste que comita
`.env`, o adiciona ao `.gitignore`, e asserta que nunca e embeddado.

**D4 - Reutilizar delete_by_file existente**
`vector_store.py:163` ja tem `delete_by_file(self, ctx: str, file_path: str)`. Para deletar
de todos os contextos, iterar sobre `COLLECTIONS` (que e `list(VALID_CONTEXTS)` em
`vector_store.py:24`). NAO adicionar nenhum novo metodo de delete. Remover qualquer codigo
proposto com `delete_file_points`, `delete_by_file_path` ou `_collections()`.

**D5 - 14GB e hipotese, nao fato confirmado**
A causa provavel e `graph_chunks: list[Chunk]` acumulando TODOS os chunks de TODOS os
arquivos em `pipeline.py:141` antes de `build_dependency_records` rodar ao final em
`pipeline.py:196-202` - e nao padding de batch nem o modelo. Remover qualquer afirmacao
causal sobre o 14GB. Tratar como hipotese a ser confirmada pelo profiling de memoria da
Phase 0. O fix (streamar `build_dependency_records` por arquivo em vez de acumular a lista
completa) e propriedade deste Spec A.

**D6 - Reconciliacao nao e bloqueada pelo hash-skip**
Com D1 (IDs estaveis), arquivos sem mudanca de hash mantem pontos Qdrant validos mesmo que
linhas tenham se deslocado (nenhum re-index necessario em hash hit). Um arquivo com hash
modificado (hash miss) dispara `delete_by_file` + re-upsert, limpando pontos de simbolos
deletados ou renomeados. E assim que o problema de orfaos e resolvido - o spec reflete isso
e nao afirma que o delete-per-file so resolve orfaos quando ocorre hash miss.

---

## Contexto

### Tres problemas identificados em codigo

**Problema 1 - Ausencia de teto de chunk em Python/TypeScript/Markdown**

`chunker.py:37` define `_MAX_CHUNK_LINES = 80`. Java aplica esse teto em metodos
(`chunker.py:170-173`), em classes sem metodos (`chunker.py:141-145`) e em
records/enums/annotations (`chunker.py:107-109`). A funcao `_split_large_node`
(`chunker.py:217-241`) implementa o split por stride de 80 linhas e recebe um `Node`
tree-sitter (nao linhas de texto - ver secao 1c abaixo).

Python e TypeScript **nao aplicam esse teto**. `_walk_python` (`chunker.py:311-360`) emite
um chunk por `function_definition` independente do tamanho. `_walk_ts` (`chunker.py:440-482`)
faz o mesmo para `function_declaration`, `method_definition` e arrow functions via
`_ts_chunk_from_node` (`chunker.py:495-512`). Markdown e texto caem no catchall de
`chunk_source` (`chunker.py:639-651`) e viram **um unico chunk** independente do tamanho
do arquivo.

Consequencia: uma funcao Python de 1 000 linhas gera um chunk de 1 000 linhas, que ocupa
muito mais do orcamento de tokens do modelo do que o teto de 80 linhas permitiria e degrada
a qualidade de retrieval (menos diversidade por query).

**Hipotese de memoria (a ser confirmada pela Phase 0):** e provavel que `graph_chunks: list[Chunk]`
(`pipeline.py:141`) acumule esses chunks gigantes em memoria ate que `build_dependency_records`
(`pipeline.py:196-202`) rode ao fim, causando picos de RSS em repos grandes. Essa hipotese
**ainda nao foi confirmada por profiling** - ver D5.

**Problema 2 - Duplo parse na extracao de call-edges**

`pipeline.py:196-197` chama `build_dependency_records(graph_chunks)`. Dentro de
`graph_extractor.py:57-76`, para cada chunk, `extract_calls(chunk)` re-parseia o campo
`chunk.content` do zero: Python via `ast.parse` (`graph_extractor.py:94`) e Java/TypeScript
via `parser.parse(source.encode("utf-8"))` (`graph_extractor.py:128`). O chunker ja
construiu essas arvores em `_chunk_python` (`chunker.py:300`) e `_chunk_typescript`
(`chunker.py:429`) mas descartou o `Node` raiz apos construir os chunks.

Resultado: cada arquivo e parseado duas vezes - uma em `chunker.py` para extrair simbolos,
outra em `graph_extractor.py` para extrair call-edges. O segundo parse e sobre `chunk.content`
(fragmento de texto, nao o arquivo inteiro), o que tambem limita a exatidao dos call-edges.

**Problema 3 - rglob sem filtragem de gitignored**

`pipeline.py:70` usa `target.rglob("*")` e entao filtra cada caminho por `path.parts`
(`pipeline.py:71`). Esse filtro cobre diretorios conhecidos (`.venv`, `node_modules`, etc.)
mas nao respeita `.gitignore`.

**O problema principal e de seguranca** (D3): o rglob pode incluir arquivos que o
`.gitignore` exclui (`.env`, `secrets.json`, chaves privadas). Se esses arquivos tiverem
extensao `.py` ou `.ts` eles sao embeddados no Qdrant. Isso e uma **vulnerabilidade de
privacidade/seguranca** que a mudanca para `git ls-files` resolve como efeito primario. O
beneficio de performance (evitar percorrer `.venv`, `node_modules`) e secundario e so deve
ser declarado como tal apos o profiling de Phase 0 confirmar que `rglob` e um gargalo
mensuravel.

Nota: `indexer.py:71-89` tem uma implementacao existente de `_iter_repo_files` que usa
`git ls-files --cached --others --exclude-standard`. Essa implementacao inclui `--others`
(untracked) e nao filtra por `git check-ignore`. A nova `iter_git_files` usara apenas
`--cached` e aplicara `git check-ignore` (ver D3 e secao 3a abaixo).

Fatos verificados no codigo:
- `chunker.py:13-15`: `ChunkType = Literal["method", "constructor", "function", "class", "interface", "enum", "annotation", "record"]`
- `chunker.py:37` (`_MAX_CHUNK_LINES = 80`), aplicado em Java em `:170-173`, `:107-109`, `:141-145`
- `chunker.py:217-241` (`_split_large_node`, recebe `Node` tree-sitter, nao linhas de texto)
- `chunker.py:300` (`_PY_PARSER.parse` descartado apos chunking)
- `chunker.py:311-360` (Python `_walk_python`: sem teto de tamanho)
- `chunker.py:417-437` (`_chunk_typescript`, parse na linha 429)
- `chunker.py:440-482` (TypeScript `_walk_ts`: sem teto de tamanho)
- `chunker.py:495-512` (`_ts_chunk_from_node`, retorna `Chunk` unico)
- `chunker.py:613-651` (`chunk_source` dispatcher; catchall linha 639-651)
- `graph_extractor.py:57-76` (`build_dependency_records` recebe chunks ja prontos)
- `graph_extractor.py:79-89` (`extract_calls` por chunk)
- `graph_extractor.py:92-105` (`_extract_python_calls` faz `ast.parse` na linha 94)
- `graph_extractor.py:127-128` (tree-sitter segundo parse Java/TS)
- `pipeline.py:59-75` (`iter_supported_files` com `rglob('*')` na linha 70)
- `pipeline.py:141` (`graph_chunks: list[Chunk]` acumula em memoria ate o fim)
- `pipeline.py:196-202` (loop sequencial `build_dependency_records` + `upsert_deps`)
- `pipeline.py:206-211` (`_chunk_id` atual usa `start_line` - D1 exige mudar para `occurrence_index`)
- `vector_store.py:24` (`COLLECTIONS = list(VALID_CONTEXTS)`)
- `vector_store.py:163` (`delete_by_file(self, ctx: str, file_path: str)` - JA EXISTE, recebe `ctx` como primeiro arg)
- `indexer.py:71-89` (`_iter_repo_files` com `git ls-files --cached --others --exclude-standard`)

---

## Decisoes de implementacao

| Tema | Decisao |
|---|---|
| Teto de chunk | `_MAX_CHUNK_LINES = 80` aplicado a **todas** as linguagens: Python, TypeScript, Markdown, texto. `_split_large_node` existente reutilizado para Python/TS (via Node). `_split_lines_into_chunks` nova funcao para Markdown e texto. |
| Markdown por secao | `.md` e tokenizado por cabecalho (`# / ## / ###`); cada secao vira um chunk com `chunk_type = "section"` (novo tipo - ver secao sobre ChunkType); se a secao ultrapassar 80 linhas, aplica `_split_lines_into_chunks`. |
| Parse unico | O chunker armazena a arvore em `Chunk.metadata["_tree"]`; `graph_extractor` reutiliza a arvore em vez de re-parsear. Arvore descartada apos extracao de call-edges (nao persiste em Qdrant). NOTA: a arvore tree-sitter em `metadata["_tree"]` **nao e thread-safe**; deve ser limpa antes de qualquer fase paralela (ver nota na secao 2c e handoff para Spec C). |
| File-walk | `iter_supported_files` substituido por wrapper de `git ls-files --cached` com `git check-ignore` (D3). Fallback para rglob somente fora de git repos, com limitacao documentada. |
| Seguranca gitignored | Nenhum arquivo gitignored e embeddado. Gate de seguranca verificavel por teste automatizado (ver secao de testes). |
| Chunk-ID estavel | `_chunk_id` muda para `uuid5(NAMESPACE_URL, f"{file_path}::{symbol}::{occurrence_index}")`. `occurrence_index` e o indice 0-based do nome do simbolo dentro do arquivo. DROP de `start_line` da chave (D1). |
| Orfaos de chunk | Com IDs estaveis (D1), arquivos sem mudanca de conteudo nao geram orfaos por deslocamento de linhas. Um arquivo com hash modificado dispara `delete_by_file` (loopando sobre `COLLECTIONS`) + re-upsert, limpando orfaos de simbolos deletados ou renomeados (D4, D6). |
| Crash-safety | Coluna `status` em `file_index` com sentinela `pending` (D2). Migracao one-shot dos 9 repos via BLUE/GREEN com recall gate antes de promover (D2). |
| _walk_calls_ts_tree para Python | `graph_extractor` atualmente usa `ast.parse` para Python. Integrar a arvore tree-sitter exige nova `_walk_calls_ts_tree` que opere sobre `tree_sitter.Tree`. Esta funcao e isolada como sub-item do PR 2 - se a complexidade for alta, dividir em PR proprio com secao de spec dedicada. |
| Recall pos-split | O teto de 80 linhas divide funcoes grandes em sub-chunks. Impacto em recall (fragmentacao de contexto) e desconhecido. O harness de recall deve ser rodado com o novo chunker no golden set ANTES do merge, como parte do gate (ver secao de recall-guard). |
| Python cap design | Checar o tamanho **antes** de appendar o chunk e chamar `_split_large_node` quando exceder o teto. O padrão de pop-e-substituir (modificar a lista em retrospecto) e descartado. |
| Ordem de entrega | (1) teto de chunks + markdown + D1 (chunk-ID); (2) parse unico + _walk_calls_ts_tree; (3) git ls-files + D2 (pending sentinel) + D3 (check-ignore) + migracao one-shot. Cada item e um PR independente com recall-guard passando. |
| YAGNI | Paralelismo e GPU nao entram neste spec. Sao candidatos para Spec C e Spec B respectivamente, dependentes do gate de Phase 0. |

---

## Componentes e mudancas

### 1. Teto universal de chunk em `chunker.py`

**1a. Python - adicionar verificacao de tamanho em `_walk_python`**

Em `_walk_python` (`chunker.py:311-360`), antes de appendar o chunk para `function_definition`,
checar o tamanho. Se exceder `_MAX_CHUNK_LINES`, passar o `Node` para `_split_large_node`
(que ja existe e aceita `Node`) em vez de criar um `Chunk` unico:

```python
# chunker.py - dentro de _walk_python, ao processar function_definition (linha ~326)
if node.type in ("function_definition",):
    symbol = _python_node_identifier(node)
    start = node.start_point[0] + 1
    end = node.end_point[0] + 1
    chunk_type: ChunkType = "method" if in_class else "function"
    if (end - start + 1) > _MAX_CHUNK_LINES:
        chunks.extend(
            _split_large_node(
                node,
                source.encode("utf-8"),
                symbol or Path(file_path).stem,
                chunk_type,
                file_path,
            )
        )
    else:
        chunks.append(
            Chunk(
                symbol=symbol or Path(file_path).stem,
                chunk_type=chunk_type,
                start_line=start,
                end_line=end,
                content="\n".join(lines[node.start_point[0] : node.end_point[0] + 1]),
                file_path=file_path,
                language="python",
            )
        )
    # Recursao para inner functions continua normalmente
```

Nota: `_split_large_node` (`chunker.py:217-241`) recebe `source: bytes` e decodifica via
`source[node.start_byte : node.end_byte].decode(errors="replace")`. A chamada acima passa
`source.encode("utf-8")` como convencao - confirmar na implementacao que `_chunk_python`
disponibiliza o `source` como bytes ou ajustar a assinatura.

**1b. TypeScript - modificar `_ts_chunk_from_node` (`chunker.py:495-512`)**

Mudar a assinatura de `_ts_chunk_from_node` para retornar `list[Chunk]`. Se
`end - start + 1 > _MAX_CHUNK_LINES`, retornar `_split_large_node(node, ...)`. Caso
contrario, retornar `[chunk_unico]`. Adaptar `_walk_ts` para `chunks.extend(...)` em vez
de `chunks.append(...)`.

**1c. `_split_lines_into_chunks` - nova funcao auxiliar para Markdown e texto**

`_split_large_node` (`chunker.py:217-241`) requer um `Node` tree-sitter e nao pode ser
usada para Markdown (sem parse tree-sitter) nem para texto puro. Uma nova funcao privada
aceita apenas linhas de texto:

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
            symbol=f"{symbol}[{idx}]",  # ALL sub-chunks named symbol[idx], including idx=0
            chunk_type=chunk_type,
            start_line=start_line_1based + i,
            end_line=start_line_1based + i + len(part) - 1,
            content="\n".join(part),
            file_path=file_path,
            language=language,
        ))
    return result
```

Relacao com `_split_large_node` (DRY): `_split_large_node` opera em bytes de um `Node`
tree-sitter e e otimizada para o caso Java/Python/TS (tem acesso ao `start_point` do nó).
`_split_lines_into_chunks` opera sobre linhas de texto ja extraidas e e necessaria para
Markdown e texto puro, onde nao ha arvore tree-sitter. Nao ha duplicacao real de logica -
as duas funcoes operam sobre tipos de entrada diferentes. Se no futuro `_split_large_node`
for refatorada para aceitar linhas (extraindo o conteudo externamente), as duas podem ser
unificadas; por ora, documentar o motivo da separacao no docstring de ambas.

**1d. ChunkType - adicionar 'section' para secoes de Markdown**

A definicao atual (`chunker.py:13-15`) e:
```python
ChunkType = Literal[
    "method", "constructor", "function", "class", "interface", "enum", "annotation", "record"
]
```

Usar `chunk_type = "class"` para secoes de Markdown e poluicao semantica (um cabecalho
Markdown nao e uma classe). Adicionar `"section"` ao Literal:

```python
ChunkType = Literal[
    "method", "constructor", "function", "class", "interface",
    "enum", "annotation", "record", "section"
]
```

`chunk_type = "section"` e usado apenas por `_chunk_markdown`. Verificar que
`_CHUNK_TYPE_TO_SYMBOL` em `indexer.py:25-34` e o campo `chunk_type` em `VectorStore.Chunk`
(`vector_store.py:32`) aceitam o novo valor. `VectorStore.Chunk.chunk_type` e `str`, logo
nao precisa de alteracao. Para `_CHUNK_TYPE_TO_SYMBOL`: a razao pela qual `"section"` nao
colide com ele hoje e que `_symbols_for_file` em `indexer.py` so processa arquivos `.py` e
`.java` via `_INDEXED_LANGUAGES`; chunks de Markdown com `chunk_type="section"` nunca chegam
a `_CHUNK_TYPE_TO_SYMBOL`. Se Markdown for adicionado a `_INDEXED_LANGUAGES` no futuro,
`_CHUNK_TYPE_TO_SYMBOL` devera ser atualizado para incluir `"section"`.

**1e. Markdown por secao - nova funcao `_chunk_markdown`**

Novo branch em `chunk_source` (`chunker.py:613-651`) para `language == "markdown"`:

```python
elif language == "markdown":
    return _chunk_markdown(source, file_path)
```

Logica de `_chunk_markdown`:
- Divide o arquivo por linhas.
- Detecta cabecalhos: linha comecando com `#` (ate `######`).
- Cada secao (cabecalho ate o proximo cabecalho de nivel igual ou superior) vira um chunk
  com `symbol = <texto do cabecalho normalizado>`, `chunk_type = "section"`, `language = "markdown"`.
- Conteudo antes do primeiro cabecalho vira chunk com `symbol = Path(file_path).stem`.
- Se a secao ultrapassar `_MAX_CHUNK_LINES`, aplica `_split_lines_into_chunks`.
- Arquivo sem nenhum cabecalho cai no comportamento atual (arquivo inteiro como 1 chunk),
  mas agora com o teto aplicado via `_split_lines_into_chunks`.

**1f. Texto (`.txt`) - aplicar teto no catchall**

Quando `language` nao for nenhum dos casos tratados (incluindo `"text"`), o catchall atual
(`chunker.py:639-651`) retorna arquivo inteiro como 1 chunk. Substituir por chamada a
`_split_lines_into_chunks` com `symbol = Path(file_path).stem` e `chunk_type = "section"`.

### 2. Parse unico em `graph_extractor.py`

A abordagem menos invasiva e armazenar a arvore tree-sitter em `Chunk.metadata["_tree"]`
durante o chunking e consumi-la em `extract_calls`. A arvore nao e serializada (nao chega
ao Qdrant - o campo `metadata` do `VectorChunk` e construido explicitamente em
`pipeline.py:107-120` e `pipeline.py:171-184`; nenhum `**chunk.metadata` e passado).

**2a. Mudanca em `chunker.py`**

Em `_chunk_python` (`chunker.py:286-308`), apos `tree = _PY_PARSER.parse(source.encode("utf-8"))`
(linha 300), propagar a arvore para cada Chunk via `metadata={"_tree": tree}`. Para chunks
derivados de `_split_large_node`, a arvore e a mesma do arquivo pai.

Em `_chunk_typescript` (`chunker.py:417-437`), apos `tree = parser.parse(source.encode("utf-8"))`
(linha 429), idem.

Para Java em `chunk_source` (`chunker.py:618-634`), onde `tree = _PARSER.parse(source_bytes)`,
propagar `metadata={"_tree": tree}` em cada chunk retornado.

**2b. Nova funcao `_walk_calls_ts_tree` em `graph_extractor.py`**

`_extract_python_calls` atual (`graph_extractor.py:92-105`) usa `ast.parse` internamente.
A arvore armazenada em `Chunk.metadata["_tree"]` pelo chunker Python e um
`tree_sitter.Tree` (nao `ast.Module`). Portanto, integrar o cache de arvore Python exige
uma nova funcao `_walk_calls_ts_tree(tree: tree_sitter.Tree) -> list[str]` que:
- Visite nos do tipo `call_expression` e `method_invocation` na arvore tree-sitter do Python.
- Extraia o nome do callee de forma analoga a `_walk_calls` (`graph_extractor.py:145-167`).
- Retorne lista de nomes filtrados por `_SKIP_CALLS`.

Escopo de nos a visitar na arvore tree-sitter-python:
- `call`: o nome do callee esta no primeiro filho ou no campo `function`.
- `attribute`: para chamadas de metodo como `obj.metodo()`, extrair o nome do atributo.

A nova funcao deve ter um teste de unidade proprio (ver secao de testes - `test_walk_calls_ts_tree`).
Se a complexidade de integracao for alta, extrair em PR proprio com secao de spec dedicada
antes de mergear com o resto do item 2.

`extract_calls` atualizado (`graph_extractor.py:79-89`):

```python
def extract_calls(chunk: Chunk) -> list[str]:
    cached_tree = chunk.metadata.get("_tree")
    if chunk.language == "python":
        if cached_tree is not None:
            calls = _walk_calls_ts_tree(cached_tree)
        else:
            calls = _extract_python_calls(chunk.content)  # fallback: re-parse via ast
    elif chunk.language == "java":
        if cached_tree is not None:
            calls: set[str] = set()
            _walk_calls(cached_tree.root_node, calls)
        else:
            calls = _extract_ts_or_java_calls(chunk.content, _JAVA_CALL_PARSER)
    elif chunk.language in {"typescript", "ts"}:
        parser = _TSX_PARSER if chunk.file_path.endswith(".tsx") else _TS_PARSER
        if cached_tree is not None:
            calls = set()
            _walk_calls(cached_tree.root_node, calls)
        else:
            calls = _extract_ts_or_java_calls(chunk.content, parser)
    else:
        calls = []
    return sorted(call for call in calls if call != chunk.symbol)
```

**2c. Limpeza de memoria e nota de thread-safety**

Apos `build_dependency_records(graph_chunks)` retornar (`pipeline.py:197`), limpar
`chunk.metadata["_tree"]` de cada chunk em `graph_chunks`:

```python
for chunk in graph_chunks:
    chunk.metadata.pop("_tree", None)
```

**NOTA DE THREAD-SAFETY PARA SPEC C:** a arvore tree-sitter armazenada em
`Chunk.metadata["_tree"]` nao e thread-safe. Qualquer fase paralela introduzida por Spec C
**nao pode** acessar `metadata["_tree"]` de chunks concorrentemente. O handoff correto e:
limpeza de `_tree` deve ocorrer antes de qualquer passo de Spec C que paralelize sobre
`graph_chunks`. Documentar explicitamente no comentario de limpeza em `pipeline.py`.

**2d. Memoria e streaming de build_dependency_records (D5)**

O fix de memoria de maior impacto esperado e streamar `build_dependency_records` por arquivo
em vez de acumular toda a lista `graph_chunks` em memoria. Proposta de implementacao:

```python
# pipeline.py - dentro do loop de index_path, logo apos upsert_batch do arquivo
if graph_store is not None:
    for record in build_dependency_records(chunks):  # chunks do arquivo atual, nao acumulado
        await graph_store.upsert_deps(
            record.symbol, calls=record.calls, called_by=record.called_by,
        )
```

Consequencia: `graph_chunks` deixa de ser necessario; remover a variavel e o bloco ao final
do loop. Isso elimina a retencao de todos os chunks em memoria simultaneamente.

**CAVEAT SEMANTICO - perda de arestas called_by cross-file:** `build_dependency_records`
agrega `called_by` sobre TODOS os chunks passados no argumento. Ao chamar por arquivo, so os
chunks do arquivo corrente sao vistos; chamadas de arquivo B para um simbolo de arquivo A
nao resultarao em `called_by` no registro de A. O grafo de dependencias perdera arestas
`called_by` entre arquivos distintos. Avaliar o impacto na qualidade do grafo durante a
Phase 0 antes de mergear este fix (gated por D5).

**Esta e uma hipotese de fix** - a Phase 0 deve confirmar via profiling se `graph_chunks` e
de fato o principal culpado pelo pico de RSS antes de mergear esta mudanca (D5).

### 3. File-walk via `git ls-files` em `pipeline.py`

**3a. Nova funcao `iter_git_files` em `axon/repo/file_walk.py`**

Extrair como funcao publica em modulo novo `axon/repo/file_walk.py`:

```python
# axon/repo/file_walk.py
def iter_git_files(
    root: Path,
    *,
    suffixes: set[str],
) -> list[Path]:
    """Lista arquivos rastreados respeitando .gitignore (D3).

    Usa `git ls-files --cached` para listar apenas arquivos commitados.
    Filtra cada caminho por `git check-ignore` para excluir arquivos que
    foram commitados e depois adicionados ao .gitignore.

    Arquivos nao-rastreados (untracked) nao sao retornados; exigem
    `git add` antes de serem indexados.

    GARANTIA DE SEGURANCA: nenhum arquivo gitignored e retornado.

    Fallback para rglob quando `git` nao esta disponivel ou `root` nao e
    um repo git. O fallback aplica EXCLUDED_DIR_NAMES mas NAO garante
    exclusao de arquivos gitignored - esta limitacao deve ser documentada
    no chamador.
    """
```

A implementacao usa `subprocess.run(["git", "-C", str(root), "ls-files", "--cached"], ...)` para
listar os tracked files e `subprocess.run(["git", "-C", str(root), "check-ignore", "--stdin"],
input="\n".join(paths))` para filtrar os que foram adicionados ao `.gitignore` pos-commit.

**3b. Modificar `iter_supported_files` em `pipeline.py:59-75`**

Substituir o corpo por chamada a `iter_git_files` quando `target` for um diretorio:

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

**3c. Preservar fallback**

`iter_git_files` cai para `rglob` quando `git` nao esta disponivel (mesmo comportamento
atual para diretorios fora de repos git), mas documenta que o fallback nao garante exclusao
de gitignored files. Fora de repos git a garantia de seguranca nao se aplica.

**3d. Atualizar `indexer.py:71-89`**

`_iter_repo_files` em `indexer.py:71-89` usa `git ls-files --cached --others
--exclude-standard` (inclui untracked, nao aplica `check-ignore`). Apos a criacao de
`iter_git_files`, substituir a implementacao local por uma chamada a `iter_git_files` de
`axon.repo.file_walk`. Isso aplica D3 consistentemente em todos os pontos de entrada de
file-walk do sistema.

### 4. Chunk-ID estavel (D1)

Mudar `_chunk_id` em `pipeline.py:206-211`:

```python
# ANTES (atual)
def _chunk_id(path: Path, chunk: Chunk) -> str:
    key = f"{path}::{chunk.symbol}::{chunk.start_line}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, key))

# DEPOIS (D1)
def _chunk_id(file_path: str, symbol: str, occurrence_index: int) -> str:
    """ID estavel: nao muda quando linhas acima do simbolo sao editadas.

    occurrence_index: indice 0-based do nome do simbolo dentro do arquivo,
    para distinguir overloads e sub-chunks (foo[0], foo[1]).
    """
    key = f"{file_path}::{symbol}::{occurrence_index}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, key))
```

O `occurrence_index` e computado pelo chamador ao iterar sobre os chunks de um arquivo,
agrupando por `chunk.symbol` e incrementando o indice a cada repeticao.

### 5. Crash-safety via sentinela pending (D2)

A tabela `file_index` (scope do Spec B - schema migration SQLite) recebe coluna `status TEXT NOT NULL DEFAULT 'done'`.

Sequencia de re-index de um arquivo (pipeline.py):

```
1. UPDATE file_index SET status='pending', sha=<novo_sha> WHERE file_path=<path>
   (ou INSERT com status='pending' se a linha nao existe)
2. await store.delete_by_file(ctx, str(file_path.resolve()))  # para cada ctx em COLLECTIONS
3. chunks = chunk_source(...)
4. await store.upsert_batch(vector_chunks)
5. UPDATE file_index SET status='done' WHERE file_path=<path>
```

Se o processo morrer entre os passos 2 e 5, na proxima run a linha tera `status='pending'`
e o arquivo sera re-indexado do zero, restaurando a consistencia.

**Migracao one-shot dos 9 repos (blue/green):**

1. Criar nova colecao Qdrant com sufixo `_v2` (ex: `knowledge_v2`).
2. Rodar `axon index <vault_root>` apontando para as colecoes `_v2`.
3. Rodar recall gate: score >= 0.90 em todas as colecoes novas.
4. Se passou: fazer alias swap (promover `_v2` como colecao principal).
5. Se falhou: manter colecoes antigas; investigar regressao antes de promover.

Runs incrementais normais apos a migracao NAO usam blue/green - apenas o pending sentinel.

### 6. Reconciliacao por arquivo (D4, D6)

`vector_store.py:163` ja tem `delete_by_file(self, ctx: str, file_path: str)`. Para deletar
de todos os contextos ao re-indexar um arquivo:

```python
# pipeline.py - dentro do loop de index_path, apos hash miss, antes de upsert
for ctx in COLLECTIONS:
    await store.delete_by_file(ctx, str(file_path.resolve()))
```

A condicao `if graph_store is not None or True:` presente em rascunhos anteriores e
**codigo morto** (o `or True` torna a condicao sempre verdadeira). A condicao correta e
simplesmente `for ctx in COLLECTIONS: ...` sem condicional, pois o delete-before-upsert
deve sempre ocorrer em hash miss.

---

## Fluxo de dados (depois)

```
iter_git_files(root)          # git ls-files --cached + check-ignore; sem rglob sobre .venv/.git
  -> [file_path, ...]
    para cada arquivo com hash miss:
      (1) write file_index status='pending'  # crash-safety D2
      (2) delete_by_file(ctx, path)          # para cada ctx em COLLECTIONS (D4)
      chunk_source(source, lang, path)
        _walk_python / _walk_ts / _chunk_markdown
          -> chunks com metadata["_tree"] = arvore parseada
      embed(chunks)
      upsert_batch(vector_chunks)            # sem _tree no payload Qdrant
      (3) write file_index status='done'     # crash-safety D2
      build_dependency_records(chunks_do_arquivo)  # stream per-file (D5 fix hipotetico)
        extract_calls(chunk)
          usa chunk.metadata["_tree"]        # sem segundo parse
        upsert_deps(...)
      limpa metadata["_tree"] dos chunks     # antes de qualquer fase paralela (Spec C)
```

Nota: `VectorChunk` em `pipeline.py:107-120` e `pipeline.py:171-184` constroem campos
explicitamente; `metadata` do `Chunk` (que contem `_tree`) **nao e copiado** para o payload
Qdrant. Confirmar na implementacao que nenhum `**chunk.metadata` e passado para `VectorChunk`.

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
   Se o baseline estiver abaixo de 0.80, o floor de 0.90 nos criterios acima e **aspiracional**
   e deve ser documentado como tal no JSON.
3. Pelo menos um gargalo confirmado com numero medido (nao estimativa) e ranqueado por
   contribuicao ao wall time total.
4. Hipotese de RSS confirmada ou refutada por profiling (D5): registrar se `graph_chunks`
   e de fato a principal causa do pico, ou identificar a causa real.
5. `gpu_available` registrado; se `false`, GPU e removida do plano Spec C completamente.
6. `stale_qdrant_points_confirmed` registrado; se `true`, delete-before-upsert e item 0 do PR.

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

4. **Gate de regressao** - o pytest carrega `tests/recall/baseline.json` e asserta:
   - `len(report.regressions) == 0`: nenhuma query que passava no baseline pode falhar apos
     a mudanca (nenhuma regressao por query individual vs baseline).
   - `BenchmarkRunSummary.score >= 0.90`: score agregado pos-mudanca.
   Se o baseline registrado for < 0.90, o floor de 0.90 e tratado como aspiracional e o gate
   exige apenas ausencia de regressao vs baseline (nao rejeita o PR so por score < 0.90).

5. **Recall e gate de entrada para o PR 1 (teto de chunks):** o teto de 80 linhas divide
   funcoes grandes em sub-chunks. O impacto em recall (potencial fragmentacao de contexto)
   e desconhecido. O harness **deve ser rodado com o novo chunker no golden set ANTES do
   merge do PR 1**, e o resultado deve ser adicionado ao `benchmarks/phase0_baseline.json`
   como `recall_post_split`. Se houver regressao (`regressions != []` ou score cair abaixo
   do baseline), o PR e bloqueado ate investigacao.

6. **Embedding em subprocess** - se o profiling da Phase 0 refutar a hipotese de acumulacao
   em `graph_chunks` e o pico de RSS vier de carga de modelo, o harness deve rodar o
   embedding em subprocess separado (coleta resultado via stdout JSON) para nao contaminar
   o processo de benchmark.

---

## Teste de seguranca: gitignored files nunca embeddados (D3)

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


@pytest.mark.asyncio
async def test_committed_then_gitignored_never_embedded(tmp_path):
    """Arquivo que foi commitado e depois adicionado ao .gitignore nao deve ser embeddado.

    Este e o caso critico de D3: sem `git check-ignore`, um arquivo que passou por
    `git ls-files --cached` ainda apareceria na listagem mesmo apos ser gitignored.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@test.com"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)

    # Commitar .env SEM gitignore
    (repo / ".env").write_text("SECRET_KEY=abc123\n")
    (repo / "main.py").write_text("def hello(): pass\n")
    subprocess.run(["git", "-C", str(repo), "add", ".env", "main.py"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "initial"], check=True)

    # Adicionar .env ao .gitignore pos-commit
    (repo / ".gitignore").write_text(".env\n")
    subprocess.run(["git", "-C", str(repo), "add", ".gitignore"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "gitignore"], check=True)

    # Act: listar arquivos via iter_git_files
    from axon.repo.file_walk import iter_git_files
    files = iter_git_files(repo, suffixes={".py", ".env"})

    # Assert: .env esta no git ls-files --cached mas deve ser excluido por check-ignore
    paths = {f.name for f in files}
    assert ".env" not in paths, ".env foi commitado mas esta gitignored: nao deve ser embeddado"
    assert "main.py" in paths
```

Ambos os testes sao **gates de seguranca**: falhar e bloqueante para merge. Devem rodar em
CI sem dependencias externas (somente `git` no PATH).

---

## Unidades (isolamento)

| Unidade | Arquivo | Dependencias | Testavel isolado? |
|---|---|---|---|
| `_split_lines_into_chunks` | `chunker.py` | nenhuma (funcao pura) | sim - entrada: linhas, saida: lista de Chunk |
| `_chunk_markdown` | `chunker.py` | `_split_lines_into_chunks` | sim - entrada: string markdown |
| Teto em `_walk_python` | `chunker.py` | `_split_large_node` (existente) | sim - funcao Python >80 linhas deve gerar N chunks |
| Teto em `_walk_ts` / `_ts_chunk_from_node` | `chunker.py` | `_split_large_node` (existente) | sim |
| `_walk_calls_ts_tree` (nova) | `graph_extractor.py` | `tree_sitter.Tree` de Python | sim - arvore pre-parseada via `_PY_PARSER.parse` |
| `extract_calls` com cache de arvore | `graph_extractor.py` | `Chunk.metadata["_tree"]` | sim - mock de chunk com arvore pre-parseada |
| `iter_git_files` | `axon/repo/file_walk.py` | subprocess `git` | sim com tmp_path + git init |
| `iter_supported_files` atualizado | `pipeline.py` | `iter_git_files` | sim - injetar mock de `iter_git_files` |
| `delete_by_file` (existente em `vector_store.py:163`) | `vector_store.py` | Qdrant client | sim com testcontainers[qdrant] |
| Reconciliacao per-file em `index_path` | `pipeline.py` | `store.delete_by_file` | sim - verificar contagem de pontos antes/depois |
| `_chunk_id` atualizado (D1) | `pipeline.py` | nenhuma | sim - funcao pura; testar que IDs iguais para mesmo symbol mesmo com start_line diferente |

---

## Verificacao end-to-end

1. **Teto de chunks**: indexar arquivo Python com funcao de 200 linhas; `chunk_source`
   deve retornar 3 chunks (linhas 1-80, 81-160, 161-200). Verificar `start_line` e
   `end_line` de cada chunk.

2. **Markdown por secao**: indexar `README.md` com 3 cabecalhos; verificar que
   `chunk_source` retorna 3+ chunks (um por secao), nenhum com mais de 80 linhas,
   cada um com `chunk_type == "section"`.

3. **Arquivo markdown gigante sem cabecalho**: indexar `.md` de 500 linhas sem `#`;
   verificar que `chunk_source` retorna 7 chunks de 80 linhas (6) + 1 de 20 linhas.

4. **Parse unico**: instrumentar `ast.parse` e `parser.parse` em `graph_extractor.py`
   com um contador; indexar 10 arquivos Python; contador deve ser 0 (todas as chamadas
   usaram a arvore cacheada).

5. **git ls-files + check-ignore**: indexar repo com `.env` commitado e depois gitignored;
   scroll Qdrant e verificar ausencia de pontos com `file_path` contendo `.env`.

6. **Reconciliacao per-file**: indexar arquivo com 2 funcoes; editar 3 linhas antes da
   primeira funcao (desloca start_line); re-indexar; `scroll(filter=file_path)` deve
   retornar exatamente 2 pontos (nao 4). Com D1, o chunk-ID nao muda por deslocamento de linha.

7. **Chunk-ID estavel (D1)**: editar 3 linhas acima de um simbolo; re-indexar; verificar que
   o `id` do ponto Qdrant para esse simbolo nao mudou.

8. **Throughput**: corpus sintetico de 500 funcoes Python de 15-30 linhas; throughput
   deve ser >= 300 chunks/s no desktop. Registrar em `benchmarks/phase0_baseline.json`
   como denominador para calculos de ganho.

9. **Recall pos-overhaul**: rodar `compare_benchmark_runs(current, baseline)` e verificar
   `regressions == []` e `score >= 0.90` (ou ausencia de regressao vs baseline se baseline < 0.90).

---

## Testes

### Unitarios (sem I/O externo)

- `test_split_lines_into_chunks`: entrada de 200 linhas -> 3 chunks; verifica `start_line`,
  `end_line`, `symbol` com sufixo `[0]`/`[1]`/`[2]`.
- `test_chunk_python_size_cap`: funcao Python de 100 linhas -> 2 chunks; funcao de 79 linhas
  -> 1 chunk (abaixo do teto).
- `test_chunk_typescript_size_cap`: mesmo padrao para `.ts`.
- `test_chunk_markdown_with_headers`: markdown com 3 cabecalhos -> 3+ chunks, nenhum > 80
  linhas, todos com `chunk_type == "section"`.
- `test_chunk_markdown_no_header_large`: 500 linhas sem cabecalho -> 7 chunks de <=80 linhas.
- `test_chunk_text_large`: `.txt` de 160 linhas -> 2 chunks de 80 linhas.
- `test_chunk_source_dispatcher_markdown`: `chunk_source(source, "markdown", path)` nao
  deve retornar chunk com `end_line - start_line + 1 > 80`.
- `test_chunk_type_section_valid`: `Chunk(chunk_type="section", ...)` deve ser aceito pelo
  `Literal` de `ChunkType` sem levantar erro de validacao Pydantic.
- `test_walk_calls_ts_tree_positive`: passar arvore tree-sitter de Python com chamadas conhecidas;
  `_walk_calls_ts_tree` deve retornar as chamadas.
- `test_walk_calls_ts_tree_fallback`: `extract_calls` com chunk sem `metadata["_tree"]` deve
  cair no fallback via `ast.parse` e ainda retornar chamadas corretas.
- `test_extract_calls_uses_cached_tree`: criar chunk com `metadata["_tree"]` preenchido por
  `_PY_PARSER.parse`; monkeypatchar `ast.parse` para levantar `AssertionError`; chamar
  `extract_calls`; deve retornar chamadas sem invocar `ast.parse`. Adicionar um segundo
  caso: chunk SEM `_tree` no metadata; `ast.parse` nao deve levantar (fallback funciona).
- `test_chunk_id_stable_across_line_shift`: gerar dois chunks com mesmo `symbol` e
  `occurrence_index` mas `start_line` diferente; verificar que `_chunk_id` retorna o mesmo
  UUID.
- `test_chunk_id_distinguishes_overloads`: dois chunks com mesmo `symbol` e `occurrence_index`
  diferente devem ter UUIDs diferentes.

### Integracao (git + filesystem)

- `test_gitignored_files_never_embedded` (gate de seguranca - descrito na secao acima).
- `test_committed_then_gitignored_never_embedded` (gate de seguranca - descrito na secao acima).
- `test_iter_git_files_fallback_no_git`: diretorio sem `.git`; `iter_git_files` deve usar
  rglob e retornar arquivos com os suffixes corretos.
- `test_delete_by_file_removes_stale_points`: com Qdrant via testcontainers, inserir 3
  pontos para `file_path=A` em ctx `knowledge`, chamar `delete_by_file("knowledge", A)`,
  scroll retorna 0 pontos.
- `test_reconcile_per_file_no_orphans`: index arquivo (2 funcoes), editar, re-index;
  scroll por file_path retorna exatamente 2 pontos.

### Benchmarks / gate

- `test_recall_guard_no_regression`: carrega `tests/recall/baseline.json`, roda harness,
  asserta `regressions == []` e `score >= 0.90` (ou ausencia de regressao se baseline < 0.90).
- `test_gitignored_never_embedded_integration`: sobe Qdrant via testcontainers, indexa repo
  com `.env` no `.gitignore`, scroll all points, asserta 0 hits com `.env` no `file_path`.

### Cobertura minima e CI

Cobertura alvo: 80%+ nas unidades novas/alteradas (`chunker.py`, `graph_extractor.py`,
`axon/repo/file_walk.py`, `pipeline.py` loop alterado).

Configurar `--cov-fail-under=80` em `pyproject.toml` apenas para os modulos tocados, nao
para o projeto inteiro (evitar regressao de cobertura nos demais modulos nao modificados).
Exemplo de configuracao em `pyproject.toml`:

```toml
[tool.pytest.ini_options]
addopts = "--cov=axon.embedder.chunker --cov=axon.embedder.graph_extractor --cov=axon.repo.file_walk --cov=axon.embedder.pipeline --cov-fail-under=80"
```

Step de CI dedicado: `pytest tests/test_chunker.py tests/test_graph_extractor.py tests/test_file_walk_security.py tests/test_pipeline.py --cov-fail-under=80`.

---

## Migracao dos 9 repos ja indexados (D2 - blue/green)

Os repos `personal` / `knowledge` foram indexados com a versao anterior do chunker (sem teto
em Python/TS, sem markdown por secao). Apos este spec ser aplicado, os chunk-IDs mudam (D1
troca `start_line` por `occurrence_index` na chave), gerando orfaos nos pontos existentes.

**Procedimento de migracao one-shot (pre-merge do primeiro PR deste spec - blue/green):**

1. Registrar `benchmarks/phase0_baseline.json` (gate Phase 0 completo).
2. Criar colecoes Qdrant com sufixo `_v2` para cada entrada em `COLLECTIONS`.
3. Rodar `axon index <vault_root>` apontando para as colecoes `_v2` (flag de colecao destino).
4. Rodar recall gate nas colecoes `_v2`: score >= 0.90.
5. Se passou: alias swap (promover `_v2` como colecao principal).
6. Se falhou: manter colecoes antigas; investigar regressao antes de promover.
7. Verificar: contagem de pontos por colecao antes e depois. Se a contagem subir alem do
   esperado (o teto gera mais chunks para arquivos grandes), e normal e esperado.

Runs incrementais normais **apos a migracao** NAO usam blue/green - apenas o pending
sentinel (D2) garante crash-safety.

---

## Fora de escopo

- Paralelismo de I/O ou CPU no pipeline (Spec C).
- GPU / troca de provider ONNX Runtime (Spec B, dependente do gate de GPU em Phase 0).
- Cache persistente de hashes entre processos (Spec B - cacheable).
- Pipelining de `upsert_deps` Redis (Spec B ou C).
- Novas linguagens (Go, Rust, Bash).
- Mudanca no modelo de embedding ou nas dimensoes dos vetores.
- Alteracao na estrutura do `VectorChunk` ou do schema Qdrant (exceto o `ctx` do `VectorStore.Chunk`).
- Schema migration SQLite para `file_index` - e escopo do Spec B, mas o pending sentinel (D2) depende dela; coordenar com Spec B ou trazer apenas a coluna `status` para este spec se necessario.

---

## Pressupostos a verificar (antes de implementar)

| Pressuposto | Verificacao barata |
|---|---|
| `rglob` e gargalo mensuravel no wall time de index | `time python -c "list(Path('<vault>').rglob('*'))"` vs `time git ls-files <vault> | wc -l`; se rglob < 1 s, o beneficio do git ls-files e **somente de seguranca** (D3), nao de performance |
| `graph_chunks` e a causa principal do pico de RSS | profiling com `psutil` ou `tracemalloc` durante index completo; registrar em `benchmarks/phase0_baseline.json` (D5) |
| Funcoes Python/TS grandes existem nos 9 repos hoje | contar chunks com end_line - start_line > 80 no repo maior |
| Arvore tree-sitter e aceitavel em `dict` via `Chunk.metadata` | testar que `Chunk(metadata={"_tree": tree})` nao levanta em `Chunk.model_validate` (Pydantic v2 aceita `Any` em `dict`) |
| `ast.parse` no `graph_extractor` e chamado sobre chunk.content (fragmento) | confirmar em `graph_extractor.py:94` que `source = chunk.content`, nao `chunk.file_path.read_text()` - **CONFIRMADO**: `_extract_python_calls(chunk.content)` em `extract_calls` linha 81 |
| `delete_by_file` existente suporta a semantica necessaria | **CONFIRMADO**: `vector_store.py:163` tem `delete_by_file(self, ctx: str, file_path: str)`; para deletar de todos os contextos, iterar sobre `COLLECTIONS` |

---

## Notas de implementacao

- Nao usar `em-dash` ou `en-dash` em nenhum comentario de codigo ou docstring gerado.
- Preservar compatibilidade de assinatura de `chunk_source(source, language, file_path)` -
  a assinatura publica nao muda.
- `_split_lines_into_chunks` e privada (prefixo `_`); nao exportar no `__init__.py`.
- O campo `metadata` de `Chunk` e `dict = Field(default_factory=dict)` (`chunker.py:48`);
  Pydantic v2 aceita qualquer valor em `dict`. A arvore tree-sitter nao e JSON-serializavel
  mas nao e persistida (so usada em memoria ate o fim do processamento do arquivo).
- Ao limpar `metadata["_tree"]` apos extracao de call-edges, usar
  `chunk.metadata.pop("_tree", None)` em vez de atribuicao direta para evitar `KeyError`
  se o chunk vier de linguagem sem arvore cacheada (markdown, texto, fallback chunks).
- A coluna `status` em `file_index` exige coordenacao com Spec B (schema migration).
  Se Spec B estiver atrasado, incluir apenas a coluna `status` neste spec para desbloquear
  o crash-safety sem depender de Spec B completo.
- `_walk_calls_ts_tree` deve ser adicionada a `graph_extractor.py` e testada isoladamente
  antes de integrar ao fluxo principal de `extract_calls`. Se a implementacao revelar
  complexidade inesperada (diferenca de gramatica tree-sitter-python vs tree-sitter-java),
  criar PR separado com secao de spec dedicada.
