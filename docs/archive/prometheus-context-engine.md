# Prometheus Context Engine
**Stack de memória e contexto para Claude Code + Copilot**
Hardware: Ryzen 7 5800X3D · RTX 4070 Ti · 32GB DDR4 · NVMe

---

## O que esse sistema faz

Quatro problemas reais no dia a dia:

1. Claude Code e Copilot não lembram o que você fez ontem.
2. Você paga tokens para mandar contexto que o modelo não precisava.
3. Nenhuma ferramenta sabe por que você tomou uma decisão arquitetural há 3 semanas.
4. Você usa dois models diferentes (Haiku para trivial, Sonnet para análise) mas hoje escolhe manualmente.

Esse sistema resolve os quatro ao mesmo tempo.

---

## Arquitetura: 5 camadas, 1 responsabilidade cada

```
[Filesystem]
     |
     v
[L1: Watcher]     Python + watchdog
     |
     v
[L2: Embedder]    Python + fastembed + CUDA
     |
     v
[L3: Store]       Qdrant (vetores) + Redis (grafo) + SQLite (sessões/ADRs)
     |
     v
[L4: Router]      LiteLLM + classificador local (phi-3 mini via Ollama)
     |
     v
[L5: MCP Server]  Python + mcp lib + FastAPI
     /        \
Claude Code   Copilot
```

Nada novo aqui por acidente. Cada tecnologia tem uma razão específica.

---

## L1: Watcher

**Linguagem:** Python

```python
# src/watcher/main.py
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import asyncio

WATCH_DIRS = [
    "~/projects/aerus-rpg",
    "~/projects/rpg-master-ai",
    "~/projects/linkedin-tool",
    "~/avangrid",           # Java/Spring
]

EXTENSIONS = {".java", ".py", ".ts", ".tsx", ".kt"}

class CodeChangeHandler(FileSystemEventHandler):
    def on_modified(self, event):
        if Path(event.src_path).suffix in EXTENSIONS:
            asyncio.create_task(pipeline.ingest(event.src_path))
```

O watcher detecta mudança, passa o caminho para o pipeline. Sem mais responsabilidades.

**Por que Python e não Rust:** você vai debugar isso às 23h. `watchdog` são 5 linhas. Um watcher em Rust com notify crate são 80 linhas e um erro de lifetime às 23h30.

---

## L2: Embedder

**Linguagem:** Python + fastembed + CUDA

```python
# src/embedder/engine.py
from fastembed import TextEmbedding
import tree_sitter_java as tsjava
import tree_sitter_python as tspython

model = TextEmbedding(
    model_name="Snowflake/snowflake-arctic-embed-l-v2.0",
    providers=["CUDAExecutionProvider"],  # 4070 Ti
    batch_size=64,
)

def chunk_java_file(path: str) -> list[Chunk]:
    # tree-sitter: extrai por método, não por arquivo inteiro
    # Um arquivo Spring de 400 linhas vira ~15 chunks de 20-30 linhas
    ...

def chunk_python_file(path: str) -> list[Chunk]:
    # tree-sitter: extrai por função e classe
    ...
```

**Por que fastembed e não ONNX Runtime direto:** fastembed abstrai o provider CUDA, gerencia o batch queue automaticamente e já suporta Arctic-v2. ONNX Runtime direto em Rust é 3 semanas de binding para o mesmo resultado.

**Ponto crítico sobre GPU:** a 4070 Ti é vantajosa na indexação inicial (batch de centenas de arquivos) e na re-indexação quando você muda vários arquivos de uma vez. Para um único arquivo salvo, a diferença de latência CPU vs GPU é pequena. O ganho real é no throughput do batch, não no hot reload individual.

---

## L3: Store

**Tecnologias:** Qdrant + Redis + SQLite

### Qdrant (vetores)

```python
# src/store/vector_store.py
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams

client = QdrantClient(":memory:")  # 32GB RAM, sem swap

# Coleções separadas por escopo
COLLECTIONS = {
    "personal":  "aerus, rpg-master-ai, linkedin-tool",
    "work":      "avangrid (Java/Spring)",
}

# Metadata por chunk
{
    "file_path":   "src/main/java/.../OrderService.java",
    "language":    "java",
    "chunk_type":  "method",           # method | class | file
    "symbol":      "processOrder",
    "project":     "avangrid",
    "git_commit":  "a3f9d21",
    "modified_at": "2026-04-19T22:00:00",
}
```

Por que Qdrant e não um banco custom em Rust: você já usou no rpg-master-ai. HNSW nativo, client Python primeiro-classe, modo in-memory que cabe nos seus 32GB sem problema.

### Redis (grafo de dependências)

```
# Mapeamento de chamadas Java
HSET dep:OrderService  calls  "[PaymentService, NotificationService]"
HSET dep:OrderService  called_by  "[OrderController, BatchJob]"

# Python
HSET dep:agent_router  imports  "[litellm, qdrant_client]"
```

Quando o Claude Code pergunta "o que usa OrderService?", você não busca vetor, você consulta o grafo direto. É $O(1)$ no Redis vs uma busca semântica que pode trazer ruído.

### SQLite (memória de sessão + ADRs)

```sql
-- Decisões arquiteturais (o que você quer lembrar)
CREATE TABLE adr (
    id          INTEGER PRIMARY KEY,
    project     TEXT,
    title       TEXT,
    context     TEXT,    -- por que precisou decidir
    decision    TEXT,    -- o que decidiu
    rationale   TEXT,    -- por que essa opção
    created_at  DATETIME
);

-- Histórico de sessão comprimido
CREATE TABLE session_memory (
    id          INTEGER PRIMARY KEY,
    project     TEXT,
    summary     TEXT,    -- Haiku comprimiu 50k tokens -> 2k
    raw_turns   INTEGER, -- quantos turns originais
    created_at  DATETIME
);

-- Git log enriquecido
CREATE TABLE code_change (
    commit_hash TEXT,
    file_path   TEXT,
    diff_summary TEXT,   -- o que mudou em linguagem natural
    why         TEXT,    -- extraído do commit message
    changed_at  DATETIME
);
```

---

## L4: Router

**Linguagem:** Python + LiteLLM

O coração do sistema. Classifica a task localmente antes de gastar um token sequer em cloud.

```python
# src/router/engine.py
import litellm

# Classificador local: phi-3 mini via Ollama, <100ms
def classify_task(content: str) -> TaskType:
    response = ollama.chat(model="phi3:mini", messages=[
        {"role": "system", "content": CLASSIFIER_PROMPT},
        {"role": "user",   "content": content}
    ])
    return TaskType(response["message"]["content"].strip())

def route(task: TaskRequest) -> str:
    task_type = classify_task(task.content)

    match task_type:
        case TaskType.TRIVIAL_COMPLETION:
            return "claude-haiku-4-5-20251001"     # ~$0.001

        case TaskType.CODE_ANALYSIS:
            if daily_cost() < BUDGET_USD:
                return "claude-sonnet-4-5"         # ~$0.01
            return "claude-haiku-4-5-20251001"     # fallback

        case TaskType.ARCHITECTURE | TaskType.DEEP_REASONING:
            # Opus só com budget explícito ou flag manual
            if task.request_opus or daily_cost() < OPUS_BUDGET:
                return "claude-opus-4-5"           # ~$0.05+
            return "claude-sonnet-4-5"

        case TaskType.LOCAL_ONLY:
            return "ollama/phi3:mini"              # $0.00

    return "claude-haiku-4-5-20251001"             # default seguro
```

**Por que LiteLLM:** uma interface para qualquer provider. Troca Sonnet por GPT-4o sem mudar uma linha de código do router. Fallback automático se um provider cair.

---

## L5: MCP Server

**Linguagem:** Python + mcp lib (Anthropic)

```python
# src/mcp/server.py
from mcp.server import Server
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("prometheus-context-engine")

@mcp.tool()
async def search_code(query: str, project: str = None, language: str = None) -> str:
    """Busca semântica no codebase. Retorna top-3 chunks relevantes."""
    results = await vector_store.search(query, filters={"project": project, "language": language})
    reranked = cross_encoder.rerank(query, results, top_k=3)  # local, <30ms
    return format_context(reranked)

@mcp.tool()
async def get_session_memory(project: str) -> str:
    """Retorna o resumo comprimido das últimas sessões no projeto."""
    return sqlite.query("SELECT summary FROM session_memory WHERE project=? ORDER BY created_at DESC LIMIT 3", project)

@mcp.tool()
async def get_dependencies(symbol: str, project: str) -> str:
    """Retorna o grafo de dependências de uma classe ou função."""
    return redis.hget(f"dep:{symbol}", "calls", "called_by")

@mcp.tool()
async def get_adrs(project: str) -> str:
    """Retorna decisões arquiteturais registradas para o projeto."""
    return sqlite.query("SELECT title, decision, rationale FROM adr WHERE project=?", project)

@mcp.tool()
async def save_adr(project: str, title: str, context: str, decision: str, rationale: str) -> str:
    """Persiste uma decisão arquitetural. Use quando tomar uma decisão de design relevante."""
    sqlite.insert("adr", {...})
    return "ADR salvo."
```

**Namespacing Claude Code vs Copilot:** as ferramentas são as mesmas, o que muda é o `context_budget`. Claude Code recebe até 8k tokens de contexto por call. Copilot recebe no máximo 2k (ele já tem o file aberto, precisa só de complemento).

```python
CONTEXT_BUDGETS = {
    "claude-code": 8000,
    "copilot":     2000,
}
```

---

## Estratégia de Token (onde o dinheiro é economizado)

### 1. Chunking por AST, nunca por arquivo

Um arquivo `OrderService.java` de 300 linhas vira 12 chunks de ~25 linhas. Você manda para o LLM apenas o método que importa, não o arquivo inteiro.

Economia estimada: **70% menos tokens por query.**

### 2. Re-ranking local antes de enviar contexto

Busca semântica retorna top-20 candidatos. Um cross-encoder local (ONNX, roda na CPU em <30ms) seleciona os top-3 mais relevantes para a pergunta específica. O LLM recebe 3 chunks, não 20.

Economia estimada: **85% de redução no contexto enviado.**

### 3. Session Memory Compressor

A cada 10 turns, Haiku roda localmente e comprime o histórico bruto em um summary de 2k tokens. O próximo turn começa com o summary, não com os 50k tokens de histórico.

```python
# Roda automaticamente a cada 10 turns
if turn_count % 10 == 0:
    summary = litellm.completion(
        model="claude-haiku-4-5-20251001",
        messages=[{"role": "user", "content": f"Comprima essa sessão em 2000 tokens: {raw_history}"}]
    )
    sqlite.upsert("session_memory", project=project, summary=summary)
```

### 4. Budget Gate no Router

Antes de qualquer call cloud, o classificador local decide o modelo. Sem intervenção manual.

```
Task trivial   -> Haiku   (~$0.001)
Code analysis  -> Sonnet  (~$0.010)
Architecture   -> Sonnet  (Opus só se pedido explicitamente)
Classificar    -> Local   ($0.000)
Re-rank        -> Local   ($0.000)
Embed          -> Local   ($0.000)
```

**Cenário sem sistema:** ~50k tokens por sessão, ~$0.50 no Sonnet.
**Cenário com sistema:** ~4k tokens por sessão, ~$0.04 no mix de modelos.
Economia por sessão: ~92%.

---

## Hosting: Hybrid é a decisão certa

**Full Self-Hosted:** custo zero, mas sem acesso remoto. Se você abrir o notebook ou precisar do sistema de outro lugar, ele não existe.

**Hybrid (recomendado):** Qdrant, Redis e SQLite rodam local. LLMs cloud via LiteLLM (você já paga Anthropic). Acesso remoto via `cloudflared tunnel` se necessário. Dados do codebase nunca saem da máquina. Essa é a configuração correta para o seu caso.

**Full Cloud:** ~$30-50/mês de infra só para Qdrant Cloud + Redis Cloud. Os dados do Avangrid (Java/Spring) saem da máquina. Não faz sentido.

```
# docker-compose.yml (stack local)
services:
  qdrant:
    image: qdrant/qdrant
    volumes: ["./qdrant_data:/qdrant/storage"]
    ports: ["6333:6333"]

  redis:
    image: redis:7-alpine
    ports: ["6379:6379"]

  ollama:
    image: ollama/ollama
    volumes: ["./ollama_models:/root/.ollama"]
    deploy:
      resources:
        reservations:
          devices: [{capabilities: [gpu]}]  # 4070 Ti

  prometheus-engine:
    build: .
    depends_on: [qdrant, redis, ollama]
    ports: ["8080:8080"]  # MCP Server
```

---

## Plano de Implementação

A regra: cada fase funciona sozinha. Não precisa terminar tudo para ter valor.

### Fase 1: Store + Watcher + Embedder (1 semana)
**Entrega:** indexação do codebase rodando. Qdrant com vetores, Redis com grafo, SQLite com estrutura.
Você já sabe usar Qdrant. O watcher é 30 linhas. O embedder é 50 linhas com fastembed.

### Fase 2: MCP Server com search_code (3-4 dias)
**Entrega:** Claude Code já consegue buscar código relevante via MCP. Esse é o ganho imediato de contexto.

### Fase 3: Session Memory + ADRs (3-4 dias)
**Entrega:** memória persistente entre sessões. `get_session_memory` e `save_adr` funcionando.

### Fase 4: Router com Budget Gate (3-4 dias)
**Entrega:** roteamento automático de modelos. Haiku para trivial, Sonnet para análise. Sem intervenção manual.

### Fase 5: Copilot + Cross-Encoder (1 semana)
**Entrega:** Copilot integrado via MCP com budget separado. Re-ranking local ativo para redução de contexto.

---

## Estrutura de Pastas

```
prometheus-engine/
├── src/
│   ├── watcher/
│   │   └── main.py
│   ├── embedder/
│   │   ├── engine.py        # fastembed + CUDA
│   │   └── chunker.py       # tree-sitter Java + Python
│   ├── store/
│   │   ├── vector_store.py  # Qdrant client
│   │   ├── graph_store.py   # Redis
│   │   └── session_store.py # SQLite (ADRs + sessões)
│   ├── router/
│   │   ├── engine.py        # LiteLLM + budget gate
│   │   └── classifier.py    # Ollama phi-3 mini
│   └── mcp/
│       └── server.py        # MCP tools
├── docker-compose.yml
├── pyproject.toml
└── CLAUDE.md                # contexto do projeto para o Claude Code
```

---

## Onde começar

Fase 1: `docker-compose up qdrant redis` e escrever o chunker Java com tree-sitter.

O chunker é o componente mais crítico. Se ele errar o tamanho e o escopo dos chunks, o resto do sistema compensa errado. Vale 2 horas de atenção antes de qualquer outra coisa.

Quer o código do chunker Java primeiro, ou prefere começar pelo docker-compose + estrutura base do projeto?
