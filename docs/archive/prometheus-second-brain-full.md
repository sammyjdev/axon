# Prometheus: Second Brain Completo
**Dev productivity + carreira + projetos pessoais como sistema único**

---

## O problema real antes da solução

Você tem quatro contextos que vivem separados e nunca conversam:

1. **Dev no trabalho** (Avangrid, Java/Spring). Decisões arquiteturais tomadas há 3 semanas somem.
2. **Dev nos projetos pessoais** (Aerus RPG, rpg-master-ai, LinkedIn tool). Nenhuma ferramenta sabe o que você já construiu.
3. **Gestão de carreira** (LinkedIn, vagas, entrevistas). Suas métricas reais estão espalhadas em conversas.
4. **Conhecimento acumulado** (posts, estudos, ADRs, anotações). Existe em algum lugar, inútil sem recuperação.

O objetivo desse sistema é fazer esses quatro contextos alimentarem um único grafo de conhecimento que qualquer ferramenta (Claude Code, Copilot, terminal, scripts) pode consultar.

---

## Mapa de Ferramentas: o que existe vs o que você precisa construir

### Camada 1: Knowledge Base (onde o conhecimento vive)

| Ferramenta | O que faz | Self-hosted? | Veredito |
|---|---|---|---|
| **Obsidian** | Vault de Markdown local, MCP pronto, 2700+ plugins, Claude Code lê diretamente | Sim (arquivos locais) | **Usar. É a fundação.** |
| Logseq | Block-level linking, open-source, bom para daily logs | Sim | Alternativa ao Obsidian, mas ecossistema menor e mobile ruim |
| Notion | Colaborativo, estruturado | Não (cloud) | Dados saem da máquina. Descarta. |

**Decisão:** Obsidian como vault central. Arquivos `.md` plain text, Claude Code lê sem MCP adicional, qualquer modelo acessa, zero lock-in. A pasta do vault é o segundo cérebro. Tudo escrito lá.

---

### Camada 2: Memória Semântica (o que o sistema lembra entre sessões)

| Ferramenta | O que faz | Self-hosted? | Veredito |
|---|---|---|---|
| **Mem0 (OpenMemory MCP)** | Extrai fatos de conversas, persiste entre sessões, 11 tools MCP, usa Qdrant + Neo4j | Sim (Docker) | **Usar para memória de conversa** |
| hmem | Memória em 5 níveis tipo humano, SQLite + FTS5, sem infra extra | Sim | Alternativa mais leve ao Mem0, sem grafo |
| Hipocampus | Hot/Warm/Cold tiers, compaction tree, ROOT.md | Sim | Mais complexo, sem ganho claro sobre Mem0 |
| Letta (ex-MemGPT) | Agente com memória infinita via paginação | Sim | Over-engineered para uso solo |

**Decisão:** Mem0 self-hosted para memória de conversa (ADRs, decisões, preferências, o que foi feito). Integra com Qdrant que você já vai ter rodando.

---

### Camada 3: LLM Router (quem responde o quê)

| Ferramenta | Tipo | Markup | Self-hosted? | Veredito |
|---|---|---|---|---|
| **LiteLLM** | Proxy open-source | Nenhum | Sim | **Usar. Controle total, dados não saem.** |
| OpenRouter | SaaS managed | 5-15% | Não | Ótimo para protótipo, markup em produção vira custo |
| Portkey | Enterprise gateway | SaaS fee | Parcial | Over-engineered para uso solo |
| Helicone | Observabilidade + proxy | SaaS fee | Parcial | Bom como add-on de analytics |

**Decisão:** LiteLLM self-hosted. O código do Avangrid não passa por servidores de terceiros. Sem markup. Você já tem o hardware para rodar. OpenRouter vale considerar só se quiser testar modelos novos sem configurar credenciais, mas não como roteador principal.

---

### Camada 4: Codebase Indexer (busca semântica no código)

| Ferramenta | O que faz | Self-hosted? | Veredito |
|---|---|---|---|
| **Custom (Qdrant + fastembed)** | Chunking Java por AST, CUDA local, grafo de deps | Sim | **Construir. Nada pronto faz chunking Java por método.** |
| Continue.dev | Indexação semântica no VS Code, tree-sitter, LanceDB | Sim (VS Code plugin) | Bom complemento para Copilot, mas não expõe MCP externo |
| Cursor | Indexação nativa, fechado | Não | Dados saem, sem controle |

**Decisão:** Continue.dev como plugin do VS Code para autocompletar, mas o indexador principal é custom com Qdrant. Continue não expõe MCP para Claude Code simultaneamente.

---

### Camada 5: Terminal e Scripts (o contexto fora do editor)

Isso é o que o plano original não cobria. Você usa terminal, Claude Code CLI, scripts Python. O sistema precisa ser acessível de lá também.

**O que falta:** um CLI local que consulta o segundo cérebro direto do terminal.

```bash
# O que você quer conseguir fazer
$ pb search "como configurei o Kafka no Avangrid"
$ pb adr add "Decidimos usar Qdrant em vez de pgvector por..."
$ pb session "aerus-rpg"   # carrega contexto do projeto no terminal
$ pb cost                   # quanto gastei hoje em tokens
```

Isso é um CLI Python simples que fala com o MCP Server. 2-3 dias de implementação. Nenhuma ferramenta pronta faz isso com o seu contexto específico.

---

## Visão Completa do Sistema

```
┌─────────────────────────────────────────────────────────┐
│                    OBSIDIAN VAULT                       │
│  /projects  /career  /adrs  /daily  /knowledge          │
│  (plain .md, Claude Code lê direto, git-tracked)        │
└────────────────────────┬────────────────────────────────┘
                         │ leitura/escrita
          ┌──────────────┼──────────────────┐
          │              │                  │
   ┌──────▼──────┐ ┌─────▼──────┐  ┌───────▼──────┐
   │  Mem0 MCP   │ │  Codebase  │  │  LiteLLM     │
   │  (memória   │ │  Indexer   │  │  Router      │
   │  de sessão) │ │  (Qdrant + │  │  (Haiku /    │
   │  Qdrant +   │ │  fastembed │  │  Sonnet /    │
   │  Neo4j      │ │  + AST)    │  │  Opus /      │
   └──────┬──────┘ └─────┬──────┘  │  Ollama)     │
          │              │         └───────┬───────┘
          └──────────────┼─────────────────┘
                         │
              ┌──────────▼──────────┐
              │   MCP GATEWAY       │
              │   (FastMCP Python)  │
              │                     │
              │  tools:             │
              │  - search_code      │
              │  - get_memory       │
              │  - get_adrs         │
              │  - get_career_ctx   │
              │  - save_decision    │
              │  - cost_report      │
              └──────────┬──────────┘
                         │
         ┌───────────────┼───────────────┐
         │               │               │
   ┌─────▼─────┐  ┌──────▼──────┐  ┌────▼─────┐
   │ Claude    │  │  Copilot    │  │  pb CLI  │
   │ Code      │  │  (VS Code)  │  │ (terminal│
   └───────────┘  └─────────────┘  └──────────┘
```

---

## Escopo do Vault (o que vai para o Obsidian)

```
vault/
├── projects/
│   ├── aerus-rpg/          # world docs, decisões, backlog
│   ├── rpg-master-ai/      # arquitetura, métricas, aprendizados
│   ├── linkedin-tool/      # plano, prompts, roadmap
│   └── avangrid/           # ADRs, contexto técnico (sem código proprietário)
├── career/
│   ├── metrics.md          # suas métricas reais (10min detection, 90% coverage)
│   ├── interview-answers/  # respostas polidas por competência
│   ├── target-companies/   # pesquisa por empresa
│   └── linkedin/           # rascunhos de posts, analytics
├── knowledge/
│   ├── java/               # padrões, armadilhas, soluções que funcionaram
│   ├── spring/
│   ├── kafka/
│   └── ai-engineering/     # o que você aprendeu construindo esse sistema
├── adrs/                   # decisões arquiteturais cross-projeto
└── daily/                  # YYYY-MM-DD.md com o que fez, decidiu, aprendeu
```

O CLAUDE.md na raiz do vault instrui Claude Code sobre como navegar e o que escrever em cada pasta. Cada sessão começa com Claude lendo o daily note de hoje e os ADRs relevantes.

---

## Ferramentas Adicionais que Valem

Duas coisas que o plano original não cobria e que fazem diferença no cenário completo:

**Langfuse (self-hosted):** observabilidade de LLM. Você vê exatamente quais prompts estão custando mais tokens, qual modelo foi chamado, latência por tool call. É o Grafana do seu stack de IA. Docker Compose, PostgreSQL. Sem custo de cloud.

```yaml
# Adicionar ao docker-compose.yml
langfuse:
  image: langfuse/langfuse:latest
  environment:
    - DATABASE_URL=postgresql://...
  ports: ["3000:3000"]
```

LiteLLM manda os logs para Langfuse automaticamente com uma linha de config. Você finalmente vê onde o dinheiro vai.

**git-cliff ou similar para changelogs automáticos:** cada commit no vault e nos projetos vira uma entrada no `code_change` do SQLite com diff summary legível. Claude Code pode ler "o que mudei nessa semana no aerus-rpg" sem você explicar.

---

## MVP: o que roda em 2 semanas

Fase 1 entrega valor imediato sem construir o sistema inteiro.

**Semana 1 (4-5 horas no total):**

```
Dia 1: Obsidian vault estruturado + CLAUDE.md
  - Criar a estrutura de pastas
  - CLAUDE.md com instruções de navegação
  - Migrar suas notas existentes para a estrutura

Dia 2-3: Mem0 + Qdrant no Docker
  - docker-compose up com Qdrant + Neo4j + Mem0
  - Configurar no Claude Code via MCP
  - Testar: iniciar sessão, trabalhar, fechar, abrir nova sessão e ver o que lembra

Dia 4-5: LiteLLM local
  - Config YAML com Haiku/Sonnet/Ollama
  - Budget gate básico ($2/dia limite)
  - Langfuse conectado para ver os custos
```

**Semana 2 (6-8 horas no total):**

```
Dia 1-2: MCP Gateway base
  - search_memories, get_adrs, save_decision
  - Conectar Claude Code + Copilot

Dia 3-4: Codebase indexer (chunker Java primeiro)
  - Indexar o projeto aerus-rpg como piloto
  - search_code funcionando no Claude Code

Dia 5: CLI pb básico
  - pb search, pb adr, pb cost
  - Funciona no terminal
```

**Entrega do MVP:** Claude Code sabe o que você fez ontem, por que tomou a decisão X, e consegue buscar código relevante no seu codebase. O terminal tem um comando para tudo isso. Custo: ~$0.03/dia em tokens.

---

## Roadmap Completo (o potencial total)

```
MVP (2 semanas)
  Obsidian vault + CLAUDE.md
  Mem0 self-hosted
  LiteLLM com budget gate
  MCP Gateway básico (memória + search)
  pb CLI básico

Fase 2 (semanas 3-4)
  Codebase indexer completo (Java + Python + TypeScript)
  Grafo de dependências Redis
  Re-ranking local com cross-encoder
  Session memory compressor automático
  Langfuse para observabilidade

Fase 3 (mês 2)
  Career context layer no vault
    Auto-sugestão de posts LinkedIn a partir do daily note
    Tracker de vagas com métricas de match
    Briefing de entrevista gerado do vault
  Git-aware change log
    "O que mudei no Aerus RPG essa semana"
    Contexto automático de commits no MCP

Fase 4 (mês 3+)
  LinkedIn tool como produto (usa o próprio sistema internamente)
  JobFlow AI retomado com o stack que você já tem rodando
  Aerus RPG com o LLM router que você construiu para uso próprio
  Reuso total: o segundo cérebro alimenta os produtos
```

A fase 4 é onde fecha o loop. O sistema que você construiu para ser mais produtivo como dev vira a infraestrutura dos seus produtos.

---

## Decisão Final: o que usar pronto vs construir

| Componente | Ferramenta | Ação |
|---|---|---|
| Knowledge base | Obsidian | Instalar e estruturar (1h) |
| Memória de sessão | Mem0 self-hosted | Docker Compose (30min) |
| LLM routing | LiteLLM | Docker Compose + YAML (1h) |
| Observabilidade | Langfuse | Docker Compose (30min) |
| Codebase indexer Java | Custom Qdrant + fastembed | **Construir** (maior esforço) |
| Grafo de dependências | Custom Redis | **Construir** |
| MCP Gateway | Custom FastMCP | **Construir** |
| pb CLI | Custom Python | **Construir** (simples) |
| Career context | Obsidian vault estruturado | Organizar (2h) |

70% do sistema já existe como ferramenta pronta. Você constrói só o que não existe: o indexador Java com chunking por AST, o grafo de dependências, e o MCP Gateway que une tudo.

---

Próximo passo concreto: estrutura do Obsidian vault + CLAUDE.md ou docker-compose.yml com o stack completo?
