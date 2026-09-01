# dec-132: the class declaration is indexed, not only its methods

- Status: Accepted
- Date: 2026-09-01
- Relates to: D5 (chunker quality is a release gate), dec-131 (ctx orders
  retrieval), GLYPH issue #55 (fixed chunks beat exact symbol spans)

## Context

AXON was measured against LlamaIndex and aider-repomap on the METRON
`code-retrieval-roundtable` (httpx corpus, 30 validated cases, shared 4000-token
cap). It lost, badly: `anchor_recall` 0.332 against LlamaIndex's 0.703, at
effectively the same context cost (1,894 vs 1,799 tokens per case).

The cause is not retrieval. `_walk_python` matched `class_definition` and
descended straight into the body without emitting anything:

```python
if node.type == "class_definition":
    for child in node.children:
        _walk_python(child, ..., in_class=True, ...)
    return
```

So the declaration line never became indexed content. Measured on the corpus:

- **0 of 1,144 chunks began with `class `.**
- Chunk types: 715 function, 423 method, 6 class - and those 6 came from the
  Java and TypeScript branches, not Python.
- **34 of 103 expected anchors (33%) were absent from the index**, every one of
  them a class declaration line: `class BaseTransport:`, `class Auth:`,
  `class DigestAuth(Auth):`.

An anchor no chunk contains is unreachable by any retrieval at any depth. The
recall ceiling was therefore **0.670**, not 1.0, and AXON was realising roughly
half of a ceiling it could not exceed.

## Decision

`class_definition` emits a header chunk - the declaration line, docstring and
class attributes, up to the line before the first method - in addition to the
per-method chunks it already produced. The methods are untouched.

Measured effect on the same corpus, same embedder, same `top_k`, same cap:

| chunking | recall | tokens/case | recall per 1k tokens |
|---|---|---|---|
| before (methods only) | 0.332 | 1,762 | 0.188 |
| **with the class header** | **0.513** | **1,431** | **0.358** |

**+0.181 recall while spending 19% fewer tokens.** It is the only variant tested
that improved both. The recall ceiling moves from 0.670 to 0.932.

## Why this is a correction and not a weakening of D5

D5 says chunker tests must not be weakened to make an implementation pass, and
`test_simple_service_no_class_level_chunk` asserted `len(class_chunks) == 0`
from the chunkers' first commit (d4d7556, 2026-04-19). No ADR justified it.

The test was inverted rather than deleted, keeping its fixture and its
neighbours, and it now asserts the class symbols by name - a stricter check than
the count it replaced. The per-method assertions in the same class are
unchanged and still pass, which is what makes the header additive.

Measurement overruled an undocumented implementation choice. That is the
direction D5 exists to allow; what it forbids is deleting a test because the
code changed under it.

## Alternatives measured and rejected

- **Prefix each method with its parent class declaration.** Ceiling 0.883
  against the header chunk's 0.932, and it inflates every method chunk while
  mixing two semantic units into one vector.
- **Fixed 40-line windows** (what LlamaIndex's `CodeSplitter` approximates).
  Ceiling 1.000 and recall 0.517 - but at 2,563 tokens per case, an efficiency
  of 0.202 against the header chunk's 0.358. Recall rises monotonically with
  window size (0.421 → 0.517 → 0.603 at 20 → 40 → 60 lines) while efficiency
  falls, which is coverage bought with volume rather than better selection.

## What this does not fix

AXON is still behind LlamaIndex, 0.513 against 0.703. The embedder is not the
cause - with identical chunks, `bge-m3` scores 0.517 against
`all-MiniLM-L6-v2`'s 0.469, so AXON's embedder is the better of the two. The
remaining gap is chunk *quality*: LlamaIndex's `CodeSplitter` respects syntactic
boundaries via tree-sitter and caps at 1,500 chars, and a naive 40-line window
carrying the same token budget reaches only 0.517. That decomposition - how much
is boundary quality versus chunk size - has not been measured.

This also confirms GLYPH issue #55's hypothesis in a second system and a second
language: the symbol boundary discards neighbouring context, and the class
declaration is exactly the neighbour a question about a class needs.
