# Prometheus: Detecção Automática de Contexto + Cross-Platform

---

## Como o sistema infere o contexto

Três sinais combinados em um score. O contexto com maior score vence.

```
QUERY ENTRA
    |
    v
┌─────────────────────────────────────────────┐
│           CONTEXT DETECTOR                  │
│                                             │
│  Sinal 1: cwd score        (peso 0.4)       │
│  Sinal 2: content score    (peso 0.4)       │
│  Sinal 3: session score    (peso 0.2)       │
│                                             │
│  vencedor = argmax(soma ponderada)          │
│  confiança = score do vencedor / total      │
└─────────────────────────────────────────────┘
    |
    v
Sempre mostra: [knowledge 87%] "virtual threads Java 21"
```

---

## Implementação do Context Detector

```python
# src/context/detector.py

from dataclasses import dataclass
from pathlib import Path
import re

CONTEXTS = ["personal", "career", "knowledge", "work", "general"]

# Mapeamento de paths para contextos
PATH_MAP = {
    "aerus-rpg":       "personal",
    "rpg-master-ai":   "personal",
    "linkedin-tool":   "personal",
    "avangrid":        "work",
    "vault/work":      "work",
    "vault/career":    "career",
    "vault/knowledge": "knowledge",
    "vault/personal":  "personal",
}

# Palavras-chave por contexto para análise de conteúdo
CONTENT_SIGNALS = {
    "knowledge": [
        # Java/Spring
        r"\bjava\b", r"\bspring\b", r"\bkafka\b", r"\bjvm\b",
        r"\bvirtual.?thread", r"\bspring.?boot\b", r"\bhibernate\b",
        r"\bmicroservice", r"\bdocker\b", r"\bkubernetes\b",
        # Python/AI
        r"\bfastembed\b", r"\bqdrant\b", r"\bollama\b", r"\brag\b",
        r"\bembedding\b", r"\bvector\b", r"\bllm\b",
        # Conceitos técnicos gerais
        r"\balgorithm\b", r"\bcomplexidade\b", r"\bbig.?o\b",
        r"\bdesign.?pattern\b", r"\bsolid\b", r"\bhexagonal\b",
    ],
    "personal": [
        r"\baer[uo]s\b", r"\brpg.?master\b", r"\blinkedin.?tool\b",
        r"\bvor'athek\b", r"\bfaction\b", r"\bmutation\b",
        r"\bworld.?doc\b", r"\bbacklog\b",
    ],
    "career": [
        r"\bvaga\b", r"\bentrevista\b", r"\bsalário\b", r"\brecruiter\b",
        r"\bcurrículo\b", r"\blinkedin\b", r"\bremote\b", r"\bjob\b",
        r"\bsenior\b.*\bposition\b", r"\bcover.?letter\b",
        r"\boffer\b", r"\bnegociar\b",
    ],
    "work": [
        r"\bavangrid\b", r"\beks\b", r"\bobservabilit\b",
        r"\bpingone\b", r"\byubico\b", r"\bestée.?lauder\b",
        r"\btcu\b", r"\bbanco.?do.?brasil\b",
    ],
    "general": [
        r"\bo que é\b", r"\bwhat is\b", r"\bexplica\b", r"\bexplain\b",
        r"\bdiferen[çc]a\b", r"\bdifference\b", r"\bcomo funciona\b",
        r"\bhow does\b",
    ],
}

@dataclass
class ContextResult:
    context: str
    confidence: float      # 0.0 a 1.0
    signals: dict          # quais sinais contribuíram
    display: str           # string formatada para mostrar ao usuário

class ContextDetector:
    def __init__(self, session_store):
        self.session = session_store

    def detect(self, query: str, cwd: str | None = None) -> ContextResult:
        scores = {ctx: 0.0 for ctx in CONTEXTS}

        # Sinal 1: diretório atual (peso 0.4)
        cwd_ctx = self._score_cwd(cwd)
        if cwd_ctx:
            scores[cwd_ctx] += 0.4

        # Sinal 2: conteúdo da query (peso 0.4)
        content_scores = self._score_content(query)
        for ctx, score in content_scores.items():
            scores[ctx] += score * 0.4

        # Sinal 3: histórico da sessão (peso 0.2)
        session_ctx = self.session.last_context()
        if session_ctx:
            scores[session_ctx] += 0.2

        # Proteção especial para work: exige sinal explícito
        # Nunca inferir work só pelo histórico
        if scores["work"] <= 0.2:
            scores["work"] = 0.0

        # Calcular vencedor e confiança
        winner = max(scores, key=scores.get)
        total = sum(scores.values()) or 1
        confidence = scores[winner] / total

        # Fallback para general se nenhum sinal forte
        if confidence < 0.35:
            winner = "general"
            confidence = 0.35

        return ContextResult(
            context=winner,
            confidence=confidence,
            signals={
                "cwd": cwd_ctx,
                "content": max(content_scores, key=content_scores.get) if content_scores else None,
                "session": session_ctx,
            },
            display=f"[{winner} {confidence:.0%}]",
        )

    def _score_cwd(self, cwd: str | None) -> str | None:
        if not cwd:
            return None
        path = cwd.lower()
        for pattern, ctx in PATH_MAP.items():
            if pattern in path:
                return ctx
        return None

    def _score_content(self, query: str) -> dict:
        query_lower = query.lower()
        scores = {}
        for ctx, patterns in CONTENT_SIGNALS.items():
            hits = sum(1 for p in patterns if re.search(p, query_lower))
            if hits > 0:
                # Normaliza: mais hits = score maior, com teto em 1.0
                scores[ctx] = min(hits / 3, 1.0)
        return scores
```

---

## Integração no MCP Gateway

Toda tool passa pelo detector antes de executar.

```python
# src/mcp/server.py

detector = ContextDetector(session_store=SessionStore())

@mcp.tool()
async def ask(
    query: str,
    cwd: str | None = None,
    ctx: str | None = None,   # override manual ainda funciona
) -> str:
    """
    Faz uma pergunta ao sistema.
    Detecta contexto automaticamente se ctx não for fornecido.
    Sempre mostra qual contexto foi escolhido.
    """

    # Detecção automática se não tiver override
    if ctx is None:
        result = detector.detect(query, cwd)
        ctx = result.context
        prefix = result.display
    else:
        prefix = f"[{ctx} manual]"

    # Proteção work: nunca inferência silenciosa
    if ctx == "work" and cwd and "work" not in cwd:
        ctx = "knowledge"
        prefix = "[knowledge auto: work requer confirmação]"

    # Roteia para o modelo correto
    model = route_model(ctx, query)
    response = await llm.complete(model=model, query=query, ctx=ctx)

    # Sempre mostra o contexto escolhido
    return f"{prefix} {response}"
```

---

## pb CLI com detecção automática

```python
# src/cli/pb.py

@app.command()
def ask(
    query: str,
    ctx: str | None = typer.Option(None, "--ctx", "-c"),
    file: Path | None = typer.Option(None, "--file", "-f"),
):
    """Faz uma pergunta. Contexto detectado automaticamente."""

    cwd = str(Path.cwd())
    result = client.ask(query=query, cwd=cwd, ctx=ctx, file=file)

    # Output sempre mostra o contexto
    typer.echo(result)
```

**Exemplos de saída:**

```bash
$ pb ask "como funciona virtual threads no Java 21"
[knowledge 91%] Virtual threads são threads leves gerenciadas pela JVM...

$ pb ask "o que é memoização"
[general 78%] Memoização é uma técnica de otimização que armazena...

$ cd ~/projects/aerus-rpg
$ pb ask "como está o sistema de combate"
[personal 94%] Com base no CONTEXT.md do aerus-rpg, o sistema de combate...

$ pb ask "vaga senior java remote"
[career 88%] Encontrei nas suas notas de career/targets...

$ pb ask "configuração EKS"
[knowledge 62%] (trabalho requer confirmação explícita)
Para acessar contexto de trabalho: pb ask --ctx=work "..."
```

---

## Tabela de decisão do detector

| cwd | conteúdo da query | sessão anterior | contexto inferido | confiança |
|---|---|---|---|---|
| `~/aerus-rpg` | "sistema de combate" | personal | personal | ~95% |
| `~/vault` | "virtual threads java" | qualquer | knowledge | ~87% |
| qualquer | "vaga senior remote" | qualquer | career | ~88% |
| `~/avangrid` | "EKS config" | work (ativo) | work (com aviso) | manual only |
| qualquer | "o que é memoização" | qualquer | general | ~78% |
| qualquer | pergunta ambígua | knowledge | knowledge | ~52% |

---

## Configuração cross-platform (Mac M1 16GB + PC)

Um único arquivo de config detecta o hardware e ajusta.

```python
# src/config/platform.py
import platform
import subprocess

def detect_platform() -> dict:
    system = platform.system()

    if system == "Darwin":
        # Mac M1/M2/M3
        mem_gb = _get_mac_memory()
        return {
            "platform":       "mac",
            "embedding_providers": ["CoreMLExecutionProvider", "CPUExecutionProvider"],
            "ollama_flash":   False,          # Metal não suporta ainda
            "max_models":     2 if mem_gb >= 32 else 1,
            "model_primary":  "gemma4:e4b",   # sempre cabe
            "model_knowledge": "gemma4:26b" if mem_gb >= 24 else "gemma4:e4b",
            "keep_alive":     "10m",          # memoria é recurso compartilhado
        }

    else:
        # Linux/Windows com NVIDIA
        vram_gb = _get_nvidia_vram()
        return {
            "platform":       "pc",
            "embedding_providers": ["CUDAExecutionProvider"],
            "ollama_flash":   True,           # CUDA suporta flash attention
            "max_models":     2 if vram_gb >= 16 else 1,
            "model_primary":  "gemma4:e4b",
            "model_knowledge": "gemma4:26b" if vram_gb >= 10 else "gemma4:e4b",
            "keep_alive":     "-1",           # VRAM dedicada, deixa carregado
        }

def _get_mac_memory() -> int:
    result = subprocess.run(
        ["sysctl", "hw.memsize"],
        capture_output=True, text=True
    )
    return int(result.stdout.split(":")[1].strip()) // (1024**3)

def _get_nvidia_vram() -> int:
    result = subprocess.run(
        ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
        capture_output=True, text=True
    )
    return int(result.stdout.strip()) // 1024
```

---

## docker-compose cross-platform

```yaml
# docker-compose.yml

services:
  qdrant:
    image: qdrant/qdrant:latest
    volumes: ["./data/qdrant:/qdrant/storage"]
    ports: ["6333:6333"]

  redis:
    image: redis:7-alpine
    volumes: ["./data/redis:/data"]
    ports: ["6379:6379"]

  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: prometheus
      POSTGRES_PASSWORD: local
      POSTGRES_DB: langfuse
    volumes: ["./data/postgres:/var/lib/postgresql/data"]

  langfuse:
    image: langfuse/langfuse:latest
    environment:
      DATABASE_URL: postgresql://prometheus:local@postgres:5432/langfuse
      NEXTAUTH_SECRET: local-secret
      NEXTAUTH_URL: http://localhost:3000
    ports: ["3000:3000"]
    depends_on: [postgres]

  # Ollama: GPU no PC, CPU/Metal no Mac
  ollama:
    image: ollama/ollama:latest
    volumes: ["./data/ollama:/root/.ollama"]
    ports: ["11434:11434"]
    environment:
      - OLLAMA_KEEP_ALIVE=${OLLAMA_KEEP_ALIVE:--1}
      - OLLAMA_NUM_PARALLEL=${OLLAMA_NUM_PARALLEL:-2}
      - OLLAMA_MAX_LOADED_MODELS=${OLLAMA_MAX_LOADED_MODELS:-1}
    profiles: ["gpu"]                        # só sobe com --profile gpu

  ollama-cpu:
    image: ollama/ollama:latest
    volumes: ["./data/ollama:/root/.ollama"]
    ports: ["11434:11434"]
    environment:
      - OLLAMA_KEEP_ALIVE=10m
      - OLLAMA_NUM_PARALLEL=1
      - OLLAMA_MAX_LOADED_MODELS=1
    profiles: ["cpu"]                        # sobe no Mac

  prometheus-engine:
    build: .
    env_file: .env.local                     # gerado pelo detect_platform()
    ports: ["8080:8080"]
    depends_on: [qdrant, redis]
```

```bash
# PC (NVIDIA)
docker compose --profile gpu up -d

# Mac M1
docker compose --profile cpu up -d
```

---

## Script de setup inicial (detecta tudo automaticamente)

```bash
#!/bin/bash
# setup.sh — roda igual no Mac e no PC

echo "Detectando plataforma..."

if [[ "$OSTYPE" == "darwin"* ]]; then
    PLATFORM="mac"
    PROFILE="cpu"
    MEM_GB=$(sysctl hw.memsize | awk '{print $2/1024/1024/1024}')
    echo "Mac detectado. Memória: ${MEM_GB}GB unificada"
else
    PLATFORM="pc"
    PROFILE="gpu"
    VRAM=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -1)
    echo "PC detectado. VRAM: ${VRAM}MB"
fi

# Gerar .env.local com config da plataforma
python3 src/config/platform.py > .env.local
echo "Config gerada em .env.local"

# Subir Docker
docker compose --profile $PROFILE up -d
echo "Infra rodando"

# Puxar modelos Ollama
ollama pull gemma4:e4b
if [[ "$PLATFORM" == "pc" ]] || [[ "$MEM_GB" -ge 24 ]]; then
    ollama pull gemma4:26b
    echo "Modelo knowledge: gemma4:26b"
else
    echo "Modelo knowledge: gemma4:e4b (M1 16GB)"
fi

echo ""
echo "Setup completo. Para começar:"
echo "  pb ask 'sua pergunta aqui'"
```

---

## Resumo: o que muda entre as duas máquinas

| Aspecto | PC (4070 Ti 12GB) | Mac M1 16GB |
|---|---|---|
| Gemma 4 E4B | CUDA, rápido | Metal, ligeiramente mais lento |
| Gemma 4 26B | CUDA, cabe | Metal, cabe (8GB de 16GB) |
| Dois modelos simultâneos | não (12GB) | não (16GB compartilhado) |
| Embedding (fastembed) | CUDA, batch rápido | CoreML, batch mais lento |
| Docker | nativo | Docker Desktop ARM |
| Ollama keep_alive | -1 (sempre carregado) | 10m (libera memória) |
| Indexação inicial | mais rápida | mais lenta (~2-3x) |
| Uso diário normal | equivalente | equivalente |

**O detector de contexto, o vault, o Mem0, o LiteLLM, o MCP Gateway e o pb CLI são idênticos nos dois.** A única diferença real é o backend de GPU e o keep_alive do Ollama.
