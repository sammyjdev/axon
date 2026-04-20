# Prometheus: Estrutura de Contextos
**Modelo B com barreira explícita no contexto de trabalho**

---

## Estrutura do Vault

```
~/vault/
│
├── CLAUDE.md                    # instruções globais para Claude Code
│
├── personal/                    # CONTEXTO: projetos pessoais
│   ├── .ctx                     # arquivo marcador: ctx=personal
│   ├── aerus-rpg/
│   │   ├── CONTEXT.md           # briefing do projeto para o MCP
│   │   ├── world/
│   │   ├── adrs/
│   │   ├── sessions/
│   │   └── backlog.md
│   ├── rpg-master-ai/
│   │   ├── CONTEXT.md
│   │   ├── architecture.md
│   │   ├── metrics.md           # 13.7x speedup, gRPC latency
│   │   └── adrs/
│   └── linkedin-tool/
│       ├── CONTEXT.md
│       ├── prompt-templates/    # v4.2 e anteriores
│       ├── roadmap.md
│       └── adrs/
│
├── career/                      # CONTEXTO: vagas e carreira
│   ├── .ctx                     # arquivo marcador: ctx=career
│   ├── CONTEXT.md
│   ├── metrics.md               # suas métricas reais e verificadas
│   ├── interviews/
│   │   ├── tell-me-about-yourself.md
│   │   ├── behavioral/
│   │   └── technical/
│   ├── targets/                 # empresas que está pesquisando
│   │   └── _template.md
│   ├── applications/            # vagas ativas
│   │   └── _template.md
│   └── linkedin/
│       ├── posts/
│       └── analytics.md
│
├── knowledge/                   # CONTEXTO: conhecimento técnico
│   ├── .ctx                     # arquivo marcador: ctx=knowledge
│   ├── java/
│   ├── spring/
│   ├── kafka/
│   ├── ai-engineering/
│   └── system-design/
│
└── work/                        # CONTEXTO: trabalho (BARREIRA EXPLÍCITA)
    ├── .ctx                     # arquivo marcador: ctx=work (RESTRICTED)
    ├── .ctxguard                # arquivo que ativa a barreira no MCP
    ├── CONTEXT.md
    ├── avangrid/
    │   ├── adrs/
    │   ├── observability/       # o que você construiu, sem código proprietário
    │   └── patterns.md          # padrões reutilizáveis (sem IP da empresa)
    └── history/                 # Yubico, Estée Lauder, TCU, Banco do Brasil
        └── metrics.md           # métricas de cada empresa para entrevistas
```

---

## CLAUDE.md (raiz do vault)

```markdown
# Prometheus Vault — Instruções para Claude Code

## Contextos disponíveis

Este vault tem quatro contextos isolados. Cada um tem regras diferentes.

| Pasta     | Contexto    | Acesso padrão | Requer flag |
|-----------|-------------|---------------|-------------|
| personal/ | Projetos    | Livre         | Não         |
| career/   | Carreira    | Livre         | Não         |
| knowledge/| Técnico     | Livre         | Não         |
| work/     | Trabalho    | BLOQUEADO     | --ctx=work  |

## Regra crítica: contexto de trabalho

**NUNCA** acesse, busque, mencione ou infira conteúdo da pasta `work/`
sem que a sessão tenha sido iniciada explicitamente com o comando:

```
pb session --ctx=work
```

Ou que o usuário tenha dito explicitamente: "acesse o contexto de trabalho".

Uma busca genérica como "como configurei Kafka" deve retornar apenas
resultados de `knowledge/` e `personal/`. Jamais de `work/`.

## Como iniciar uma sessão

1. Leia o daily note de hoje em `daily/YYYY-MM-DD.md` se existir.
2. Leia o `CONTEXT.md` da pasta do projeto ativo.
3. Não carregue contexto de outros projetos a não ser que seja pedido.

## Como encerrar uma sessão

Ao final, escreva um resumo da sessão no daily note de hoje:
- O que foi feito
- Decisões tomadas (salvar como ADR se for arquitetural)
- Próximos passos

## Escrita no vault

- ADRs vão em `<projeto>/adrs/YYYY-MM-DD-titulo.md`
- Métricas verificadas vão em `career/metrics.md`
- Padrões técnicos aprendidos vão em `knowledge/<tecnologia>/`
- Nunca escreva código proprietário do trabalho no vault

## Formato de ADR

```
# ADR-XXX: Título

**Data:** YYYY-MM-DD
**Projeto:** nome
**Status:** Aceito | Proposto | Deprecado

## Contexto
Por que essa decisão precisou ser tomada.

## Decisão
O que foi decidido.

## Justificativa
Por que essa opção e não as alternativas.

## Consequências
O que muda. O que fica mais difícil.
```
```

---

## Isolamento no MCP Gateway

A barreira explícita para `work/` é implementada em duas camadas.

### Camada 1: Qdrant collections separadas

```python
# src/store/collections.py

COLLECTIONS = {
    "personal":  {"restricted": False},
    "career":    {"restricted": False},
    "knowledge": {"restricted": False},
    "work":      {"restricted": True},   # barreira aqui
}

def get_search_collections(ctx: str | None) -> list[str]:
    """
    Retorna as collections disponíveis para busca.
    work só entra se ctx='work' for passado explicitamente.
    """
    if ctx == "work":
        return ["work"]

    # qualquer busca sem ctx explícito nunca vê work
    return ["personal", "career", "knowledge"]
```

### Camada 2: MCP tools com escopo obrigatório

```python
# src/mcp/server.py

@mcp.tool()
async def search_code(
    query: str,
    ctx: str | None = None,      # nunca tem default para work
    language: str | None = None,
) -> str:
    """
    Busca semântica no codebase.

    ctx: personal | career | knowledge | work
    Para acessar work, ctx='work' é obrigatório e explícito.
    Sem ctx, busca em personal + career + knowledge.
    """
    collections = get_search_collections(ctx)
    results = await vector_store.search(
        query,
        collections=collections,
        language=language,
    )
    reranked = cross_encoder.rerank(query, results, top_k=3)
    return format_context(reranked, show_ctx=True)


@mcp.tool()
async def get_memory(
    query: str,
    ctx: str | None = None,
) -> str:
    """
    Busca na memória persistente (Mem0).
    work só retorna se ctx='work' explícito.
    """
    scope_filter = {"ctx": ctx} if ctx else {"ctx__ne": "work"}
    return await mem0.search(query, filters=scope_filter)


@mcp.tool()
async def get_adrs(
    project: str,
    ctx: str | None = None,
) -> str:
    """
    Retorna ADRs de um projeto.
    Projetos de work só acessíveis com ctx='work'.
    """
    if project in WORK_PROJECTS and ctx != "work":
        return "Contexto de trabalho requer ctx='work' explícito."
    return sqlite.query(
        "SELECT * FROM adr WHERE project = ?", project
    )
```

### Camada 3: pb CLI com confirmação

```python
# src/cli/pb.py

@app.command()
def session(ctx: str = typer.Argument(...)):
    """Inicia uma sessão no contexto especificado."""

    if ctx == "work":
        confirm = typer.confirm(
            "Ativando contexto de trabalho (Avangrid). Confirma?",
            default=False,
        )
        if not confirm:
            raise typer.Abort()
        typer.echo("Contexto de trabalho ativo. Use pb session --ctx=personal para sair.")

    os.environ["PROMETHEUS_CTX"] = ctx
    typer.echo(f"Sessão iniciada: {ctx}")
```

---

## Mem0: collections por contexto

```python
# src/store/mem0_config.py

MEM0_CONFIG = {
    "vector_store": {
        "provider": "qdrant",
        "config": {
            "collection_name": "prometheus_{ctx}",  # uma por contexto
            "host": "localhost",
            "port": 6333,
        }
    },
    "graph_store": {
        "provider": "neo4j",
        "config": {
            "url": "bolt://localhost:7687",
            "username": "neo4j",
            "password": "...",
        }
    },
    "llm": {
        "provider": "litellm",
        "config": {"model": "claude-haiku-4-5-20251001"}
    }
}

# Na prática: 4 collections no Qdrant
# prometheus_personal
# prometheus_career
# prometheus_knowledge
# prometheus_work   <- só acessada com ctx=work explícito
```

---

## docker-compose.yml

```yaml
services:

  qdrant:
    image: qdrant/qdrant:latest
    volumes:
      - ./data/qdrant:/qdrant/storage
    ports:
      - "6333:6333"

  neo4j:
    image: neo4j:5-community
    environment:
      - NEO4J_AUTH=neo4j/prometheus-local
    volumes:
      - ./data/neo4j:/data
    ports:
      - "7474:7474"
      - "7687:7687"

  redis:
    image: redis:7-alpine
    volumes:
      - ./data/redis:/data
    ports:
      - "6379:6379"

  ollama:
    image: ollama/ollama:latest
    volumes:
      - ./data/ollama:/root/.ollama
    ports:
      - "11434:11434"
    deploy:
      resources:
        reservations:
          devices:
            - capabilities: [gpu]

  langfuse:
    image: langfuse/langfuse:latest
    environment:
      - DATABASE_URL=postgresql://prometheus:local@postgres:5432/langfuse
      - NEXTAUTH_SECRET=local-secret
      - NEXTAUTH_URL=http://localhost:3000
    ports:
      - "3000:3000"
    depends_on: [postgres]

  postgres:
    image: postgres:16-alpine
    environment:
      - POSTGRES_USER=prometheus
      - POSTGRES_PASSWORD=local
      - POSTGRES_DB=langfuse
    volumes:
      - ./data/postgres:/var/lib/postgresql/data

  prometheus-engine:
    build: .
    environment:
      - QDRANT_URL=http://qdrant:6333
      - NEO4J_URL=bolt://neo4j:7687
      - REDIS_URL=redis://redis:6379
      - OLLAMA_URL=http://ollama:11434
      - LANGFUSE_URL=http://langfuse:3000
    ports:
      - "8080:8080"
    depends_on:
      - qdrant
      - neo4j
      - redis
      - ollama
```

---

## Regras de escrita por contexto

O que pode e não pode ir em cada pasta do vault.

| Contexto | Pode escrever | Nunca escrever |
|---|---|---|
| `personal/` | código, ADRs, world docs, backlog | dados de clientes, IP de empregador |
| `career/` | métricas suas, respostas de entrevista, pesquisa de empresa | salário atual, dados de colegas |
| `knowledge/` | padrões técnicos, soluções, aprendizados | código proprietário com contexto de empresa |
| `work/` | ADRs de decisões suas, padrões que você criou | código-fonte proprietário, dados de cliente, arquitetura interna completa |

A distinção no `work/`: você pode registrar *o que você decidiu e por quê* (isso é seu). Não pode registrar *o código da empresa* (isso não é seu).

---

## pb CLI: comandos por contexto

```bash
# Iniciar sessão em um contexto
pb session personal
pb session career
pb session knowledge
pb session work          # pede confirmação

# Busca com escopo
pb search "padrão Kafka"                    # busca em personal+career+knowledge
pb search "padrão Kafka" --ctx=knowledge    # só knowledge
pb search "EKS setup" --ctx=work           # só work, requer sessão ativa

# ADRs
pb adr list --project=aerus-rpg
pb adr add --project=aerus-rpg             # abre editor
pb adr list --ctx=work --project=avangrid  # requer ctx=work

# Carreira
pb career metrics                          # suas métricas compiladas
pb career brief "Bilt Rewards"            # brief de empresa para entrevista
pb career interview "sistema distribuído" # puxa respostas relevantes

# Custo
pb cost today
pb cost week --breakdown                   # por contexto
```

---

## Próximo passo

A estrutura do vault e o CLAUDE.md são o ponto de partida, custam 1-2 horas e
já entregam valor antes de qualquer código: Claude Code passa a ter contexto
persistente e isolado por projeto na próxima sessão que você abrir.

Depois disso: docker-compose up e o MCP Gateway com as collections separadas.
