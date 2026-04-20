# Knowledge: Daily vs Deep Study

---

## Estrutura de pastas

```
~/vault/knowledge/
│
├── daily/                        # captura do dia a dia
│   ├── java/
│   ├── spring/
│   ├── kafka/
│   ├── python/
│   ├── ai-engineering/
│   └── system-design/
│
└── deep/                         # estudo acumulativo
    ├── java/
    ├── spring/
    ├── kafka/
    ├── python/
    ├── ai-engineering/
    └── system-design/
```

---

## O que vai para cada lado

| Tipo de conteúdo | Destino | Exemplo |
|---|---|---|
| Bug encontrado | daily | `@Transactional` ignorado em chamada interna |
| Interação de libs | daily | fastembed + CUDA no Windows exige driver específico |
| Comportamento inesperado | daily | Kafka consumer não rebalanceia sem heartbeat |
| Conceito rápido (TIL) | daily | Records Java não aceitam herança |
| HOW-TO gerado automaticamente | daily | Como configurar Testcontainers com PostgreSQL |
| Fundamento de uma tecnologia | deep | Como o JVM gerencia threads internamente |
| Comparação de abordagens | deep | Virtual Threads vs Coroutines vs Reactive |
| Estudo de sistema externo | deep | Como o Kafka garante exactly-once semantics |
| Preparação para entrevista técnica | deep | System design: como projetaria o Twitter |
| Acumulação de padrões | deep | Padrões de resiliência em microservices |

---

## Template: daily (TIL + HOW-TO, já definidos)

Sem mudança. O fluxo D+E existente alimenta `daily/` diretamente.

```bash
# vai para knowledge/daily/java/
pb til "records java não aceitam herança"
pb howto --from til-2026-04-19-records-heranca.md
```

---

## Template: deep (estudo acumulativo)

Notas deep não têm data no nome. Crescem com o tempo.
Uma nota por tópico, editada múltiplas vezes.

```markdown
---
tags: [java, concurrency]
type: deep
created: 2026-04-19
updated: 2026-04-19
maturity: seed        # seed | growing | solid
---

# Virtual Threads (Java 21)

## O que são
Threads leves gerenciadas pela JVM, não pelo OS.
Permitem milhões de threads concorrentes sem overhead
de context switch de kernel thread.

## Como funcionam internamente
A JVM mapeia N virtual threads em M platform threads
(carrier threads). Quando uma virtual thread bloqueia
em I/O, a carrier thread é liberada para outra.

```
Virtual Thread 1  ─┐
Virtual Thread 2  ─┤── Carrier Thread (platform) ── OS Thread
Virtual Thread 3  ─┘
```

## Quando usar
- I/O bound com alta concorrência (HTTP, JDBC, arquivos)
- Substituição direta de thread pools em código legado

## Quando NÃO usar
- CPU bound: sem ganho, carrier thread fica ocupada
- Código com ThreadLocal pesado: pode vazar entre requests

## Comparação com alternativas

| Abordagem | Caso ideal | Complexidade |
|---|---|---|
| Virtual Threads | I/O bound, migração legado | baixa |
| Reactive (WebFlux) | I/O bound, throughput máximo | alta |
| Coroutines (Kotlin) | novo código, composição | média |

## Experimentos pessoais
- [[daily/java/howto-2026-04-19-virtual-threads-spring.md]]
  Configuração no Spring Boot 3.2+

## Gaps que ainda tenho
- Como Virtual Threads interagem com Synchronized blocks
- Structured Concurrency: ainda não usei em produção

## Fontes
- JEP 444 (Virtual Threads)
- Experimento no rpg-master-ai: pipeline de embedding
```

---

## Sistema de sugestões: como funciona

O sistema analisa suas notas `daily/` e identifica padrões
que sugerem gaps no `deep/`. Roda uma vez por semana
via cron ou `pb deep --suggest`.

```python
# src/vault/deep_suggester.py

SUGGESTION_PROMPT = """
Você é um mentor técnico analisando as notas diárias de um
Senior Java Engineer.

Notas daily da semana:
{daily_notes}

Notas deep existentes:
{deep_index}

Identifique até 3 gaps de conhecimento: situações onde o
engenheiro resolveu um problema sem entender o fundamento
por trás, ou onde múltiplos TILs apontam para o mesmo
conceito não estudado em profundidade.

Para cada gap, responda em JSON:
{
  "topic": "nome do tópico",
  "why": "por que é um gap (cite as notas daily)",
  "suggested_title": "título para nota deep",
  "starting_questions": ["pergunta 1", "pergunta 2"]
}

Responda apenas com array JSON, sem texto extra.
"""

async def suggest_deep_topics() -> list[dict]:
    vault = Path.home() / "vault" / "knowledge"

    # Coleta TILs e HOW-TOs da semana
    week_ago = date.today() - timedelta(days=7)
    daily_notes = [
        f.read_text() for f in vault.rglob("daily/**/*.md")
        if _created_after(f, week_ago)
    ]

    # Índice das notas deep existentes (só títulos)
    deep_index = [
        f.stem for f in vault.rglob("deep/**/*.md")
    ]

    if not daily_notes:
        return []

    response = await litellm.acompletion(
        model="ollama/gemma4:26b",    # local, análise mais profunda
        messages=[{
            "role": "user",
            "content": SUGGESTION_PROMPT.format(
                daily_notes="\n---\n".join(daily_notes[:20]),
                deep_index="\n".join(deep_index),
            )
        }],
        max_tokens=1000,
    )

    raw = response.choices[0].message.content
    return json.loads(raw)
```

---

## Saída do suggester

```bash
$ pb deep --suggest

Analisando 12 notas daily da semana...

Gap 1: Spring AOP e Proxy Pattern
  Por que: 3 TILs sobre @Transactional falhando em contextos
  diferentes. Você resolve o sintoma mas não o mecanismo.
  Sugestão: deep/spring/spring-aop-proxy-internals.md
  Perguntas iniciais:
    - Como o Spring cria proxies em runtime?
    - Quando CGLIB vs JDK proxy?
    - Por que self-invocation quebra @Transactional?

Gap 2: Kafka Consumer Group Rebalancing
  Por que: 2 HOW-TOs sobre consumer lag e partition
  assignment. Padrão sugere lacuna no fundamento.
  Sugestão: deep/kafka/consumer-group-rebalancing.md
  Perguntas iniciais:
    - O que dispara um rebalance?
    - Como minimizar stop-the-world durante rebalance?
    - Diferença entre eager e cooperative rebalancing?

Criar notas? [s/N]
```

Se confirmar, cria os arquivos com o frontmatter preenchido
e as perguntas como seções vazias para você responder.

---

## Maturidade das notas deep

O campo `maturity` no frontmatter evolui com o tempo.

```
seed    → nota criada, perguntas iniciais sem resposta
growing → algumas seções preenchidas, gaps identificados
solid   → fundamento compreendido, experimentos pessoais
```

```bash
# Ver notas por maturidade
pb deep --list seed      # o que precisa de atenção
pb deep --list growing   # em progresso
pb deep --list solid     # consolidado
```

---

## Roteamento de busca por tipo

O detector de contexto usa o tipo da nota para rotear
o modelo correto.

```python
# src/context/detector.py (adição)

def route_knowledge_query(query: str, note_type: str) -> str:
    """
    daily: resposta rápida, Gemma E4B local
    deep:  análise profunda, Gemma 26B local
    """
    if note_type == "daily":
        return "ollama/gemma4:e4b"
    if note_type == "deep":
        return "ollama/gemma4:26b"
    return "ollama/gemma4:e4b"   # default
```

```bash
# Busca automática nos dois
$ pb search "kafka rebalancing"
[knowledge/daily 82%] HOW-TO: Consumer lag silencioso sem alertas
[knowledge/deep  71%] Kafka Consumer Group Rebalancing (seed)

# Busca só no deep
$ pb search "kafka" --type=deep
```

---

## Fluxo completo da semana

```
Segunda a sexta (durante sessões):
  pb til "..."           → knowledge/daily/<tech>/
  Automático no commit   → TILs viram HOW-TOs se tiverem substância

Fim de semana (opcional, 10-15 min):
  pb deep --suggest      → sistema aponta gaps com base na semana
  Você escolhe 1 gap     → abre nota deep com perguntas iniciais
  Responde o que sabe    → maturity: seed → growing

Ao longo do tempo:
  Notas growing acumulam experimentos e links
  Quando você domina     → maturity: growing → solid
  Solid vira referência  → busca semântica sempre retorna como contexto
```

---

## Estrutura final do knowledge

```
~/vault/knowledge/
│
├── daily/
│   ├── java/
│   │   ├── til-2026-04-19-records-heranca.md
│   │   └── howto-2026-04-19-testcontainers-postgres.md
│   ├── spring/
│   │   └── howto-2026-04-19-transactional-self-invocation.md
│   └── kafka/
│       └── til-2026-04-20-consumer-lag-silent.md
│
└── deep/
    ├── java/
    │   └── virtual-threads.md              # maturity: growing
    ├── spring/
    │   └── spring-aop-proxy-internals.md   # maturity: seed
    └── kafka/
        └── consumer-group-rebalancing.md   # maturity: seed
```
