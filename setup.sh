#!/usr/bin/env bash
set -euo pipefail

# setup.sh — bootstrap da infra Prometheus
# Funciona igual no Mac M1 e no PC com NVIDIA

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

REMOTE_INFRA_HOST="${PROMETHEUS_INFRA_HOST:-${PROMETHEUS_DESKTOP_HOST:-}}"

echo "==> Detectando plataforma..."

if [[ "$OSTYPE" == "darwin"* ]]; then
    PLATFORM="mac"
    COMPOSE_PROFILE="cpu"
    MEM_BYTES=$(sysctl -n hw.memsize)
    MEM_GB=$((MEM_BYTES / 1024 / 1024 / 1024))
    echo "    Mac detectado — ${MEM_GB}GB memória unificada"
else
    PLATFORM="pc"
    COMPOSE_PROFILE="gpu"
    if command -v nvidia-smi &>/dev/null; then
        VRAM_MB=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -d ' ')
        VRAM_GB=$((VRAM_MB / 1024))
        echo "    PC detectado — ${VRAM_GB}GB VRAM (NVIDIA)"
    else
        echo "    PC detectado — nvidia-smi não encontrado, usando CPU fallback"
        COMPOSE_PROFILE="cpu"
    fi
fi

echo ""
echo "==> Gerando .env.local a partir da plataforma..."
GENERATED_ENV="$(mktemp)"
python3 src/prometheus/config/platform.py > "$GENERATED_ENV"

merge_env_file() {
    local source_file=$1
    local target_file=$2
    local mode=${3:-replace}

    [[ -f "$source_file" ]] || return 0
    python3 src/prometheus/config/platform.py --merge-env "$source_file" "$target_file" "$mode"
}

if [[ -f ".env.local" ]]; then
    echo "    Preservando overrides existentes de .env.local..."
    merge_env_file ".env.local" "$GENERATED_ENV"
fi

if [[ -f ".env.example" ]]; then
    echo "    Mesclando defaults de .env.example..."
    merge_env_file ".env.example" "$GENERATED_ENV" "append-missing"
fi

mv "$GENERATED_ENV" .env.local

echo "    .env.local gerado"

# Verificação básica de acessibilidade
check_service() {
    local name=$1
    local url=$2
    if curl -sf "$url" &>/dev/null; then
        echo "    [OK] $name"
    else
        echo "    [WARN] $name não respondeu em $url — pode ainda estar iniciando"
    fi
}

if [[ -n "$REMOTE_INFRA_HOST" ]]; then
    echo ""
    echo "==> Modo infra remota detectado: $REMOTE_INFRA_HOST"
    echo "    Docker local e pull de modelos serão ignorados."
    echo ""
    echo "==> Validando serviços remotos..."
    check_service "Qdrant"   "http://${REMOTE_INFRA_HOST}:6333/collections"
    check_service "Langfuse" "http://${REMOTE_INFRA_HOST}:3000"
    check_service "Ollama"   "http://${REMOTE_INFRA_HOST}:11434/api/tags"
else
    echo ""
    echo "==> Criando diretórios de dados..."
    mkdir -p data/{qdrant,redis,neo4j,postgres,ollama}

    echo ""
    echo "==> Subindo stack Docker com profile: $COMPOSE_PROFILE"
    docker compose --profile "$COMPOSE_PROFILE" up -d

    echo ""
    echo "==> Aguardando serviços ficarem healthy..."
    sleep 5

    check_service "Qdrant"   "http://localhost:6333/collections"
    check_service "Redis"    "http://localhost:6379" || true  # redis não é HTTP
    check_service "Langfuse" "http://localhost:3000"
    check_service "Ollama"   "http://localhost:11434/api/tags"

    echo ""
    echo "==> Puxando modelos Ollama necessários..."
    ollama pull phi3:mini
    ollama pull gemma4:e4b

    if [[ "$PLATFORM" == "pc" ]] || { [[ "$PLATFORM" == "mac" ]] && [[ "${MEM_GB:-0}" -ge 24 ]]; }; then
        echo "    Puxando gemma4:26b (modelo knowledge)..."
        ollama pull gemma4:26b
    else
        echo "    Memória insuficiente para gemma4:26b — usando gemma4:e4b como fallback"
    fi
fi

echo ""
echo "==> Setup concluído."
echo ""
echo "    Próximos passos:"
echo "      1. Preencha ANTHROPIC_API_KEY em .env.local"
if [[ -n "$REMOTE_INFRA_HOST" ]]; then
    echo "      2. Confirme os endpoints remotos em .env.local"
else
    echo "      2. Indexe o vault: pb index --ctx personal"
fi
echo "      3. Consulte: pb ask 'sua pergunta aqui'"
echo ""
