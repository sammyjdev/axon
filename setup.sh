#!/usr/bin/env bash
set -euo pipefail

# setup.sh — bootstrap da infra Prometheus
# Funciona igual no Mac M1 e no PC com NVIDIA

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

REMOTE_INFRA_HOST="${AXON_INFRA_HOST:-${AXON_DESKTOP_HOST:-}}"
PYTHONPATH_PREFIX="$SCRIPT_DIR/src${PYTHONPATH:+:$PYTHONPATH}"

resolve_setup_mode() {
    local remote_host=${1:-}
    PYTHONPATH="$PYTHONPATH_PREFIX" python3 - "$remote_host" <<'PY'
import shutil
import sys

from axon.config.platform import build_doctor_report, detect_platform
from axon.config.runtime import get_runtime_sources, load_runtime_config

remote_host = sys.argv[1].strip()
runtime = load_runtime_config()
sources = get_runtime_sources()
platform_config = detect_platform()
report = build_doctor_report(
    runtime,
    platform_config,
    docker_available=shutil.which("docker") is not None,
    ollama_available=shutil.which("ollama") is not None,
    sources=sources,
)
source = sources.get("mode", "default")
selected_mode = runtime.mode if source in {"env", "toml"} else report.recommended_mode
if remote_host and source == "default":
    selected_mode = "remote-infra"
print(f"{selected_mode}\t{source}\t{report.recommended_mode}")
PY
}

resolve_setup_plan() {
    local runtime_mode=$1
    local remote_host=${2:-"-"}
    PYTHONPATH="$PYTHONPATH_PREFIX" python3 - "$runtime_mode" "$remote_host" <<'PY'
import sys

from axon.config.platform import build_setup_plan, detect_platform

runtime_mode = sys.argv[1]
remote_host = sys.argv[2]
if remote_host == "-":
    remote_host = None

plan = build_setup_plan(
    runtime_mode=runtime_mode,
    platform_config=detect_platform(),
    remote_infra_host=remote_host,
)
compose_profile = plan.compose_profile or "-"
start_local_stack = "1" if plan.start_local_stack else "0"
validate_remote = "1" if plan.validate_remote_services else "0"
pull_models = ",".join(plan.pull_models)

print(f"{compose_profile}\t{start_local_stack}\t{validate_remote}\t{pull_models}")
PY
}

upsert_env_var() {
    local target_file=$1
    local key=$2
    local value=$3

    PYTHONPATH="$PYTHONPATH_PREFIX" python3 - "$target_file" "$key" "$value" <<'PY'
from pathlib import Path
import sys

target_path = Path(sys.argv[1])
key = sys.argv[2]
value = sys.argv[3]

lines = target_path.read_text(encoding="utf-8").splitlines() if target_path.exists() else []
updated: list[str] = []
replaced = False
prefix = f"{key}="
for line in lines:
    if line.startswith(prefix):
        updated.append(f"{key}={value}")
        replaced = True
    else:
        updated.append(line)
if not replaced:
    updated.append(f"{key}={value}")
target_path.write_text("\n".join(updated) + "\n", encoding="utf-8")
PY
}

IFS=$'\t' read -r SETUP_MODE MODE_SOURCE RECOMMENDED_MODE <<< "$(resolve_setup_mode "${REMOTE_INFRA_HOST:-}")"
IFS=$'\t' read -r PLAN_COMPOSE_PROFILE PLAN_START_LOCAL_STACK PLAN_VALIDATE_REMOTE PLAN_PULL_MODELS <<< "$(resolve_setup_plan "$SETUP_MODE" "${REMOTE_INFRA_HOST:--}")"

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
        if VRAM_MB=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -d ' '); then
            VRAM_GB=$((VRAM_MB / 1024))
            echo "    PC detectado — ${VRAM_GB}GB VRAM (NVIDIA)"
        else
            echo "    PC detectado — nvidia-smi falhou, usando CPU fallback"
            COMPOSE_PROFILE="cpu"
        fi
    else
        echo "    PC detectado — nvidia-smi não encontrado, usando CPU fallback"
        COMPOSE_PROFILE="cpu"
    fi
fi

if [[ "$SETUP_MODE" == "hybrid-local" ]]; then
    COMPOSE_PROFILE="cpu"
fi
if [[ "$PLAN_COMPOSE_PROFILE" != "-" ]]; then
    COMPOSE_PROFILE="$PLAN_COMPOSE_PROFILE"
fi

echo ""
echo "==> Modo de setup: $SETUP_MODE"
if [[ "$MODE_SOURCE" == "default" ]]; then
    echo "    Sem modo explícito configurado; usando recomendação automática: $RECOMMENDED_MODE"
else
    echo "    Modo explícito carregado via $MODE_SOURCE"
fi

echo ""
echo "==> Gerando .env.local a partir da plataforma..."
GENERATED_ENV="$(mktemp)"
PYTHONPATH="$PYTHONPATH_PREFIX" python3 - <<'PY' > "$GENERATED_ENV"
from axon.config.platform import _to_dotenv, detect_platform

print(_to_dotenv(detect_platform()), end="")
PY

merge_env_file() {
    local source_file=$1
    local target_file=$2
    local mode=${3:-replace}

    [[ -f "$source_file" ]] || return 0
    PYTHONPATH="$PYTHONPATH_PREFIX" python3 - "$source_file" "$target_file" "$mode" <<'PY'
from pathlib import Path
import sys

from axon.config.platform import merge_env_files

merge_env_files(Path(sys.argv[1]), Path(sys.argv[2]), mode=sys.argv[3])
PY
}

if [[ -f ".env.local" ]]; then
    echo "    Preservando overrides existentes de .env.local..."
    merge_env_file ".env.local" "$GENERATED_ENV"
fi

if [[ -f ".env.example" ]]; then
    echo "    Mesclando defaults de .env.example..."
    merge_env_file ".env.example" "$GENERATED_ENV" "append-missing"
fi

upsert_env_var "$GENERATED_ENV" "AXON_RUNTIME_MODE" "$SETUP_MODE"
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

if [[ "$SETUP_MODE" == "remote-infra" ]]; then
    if [[ -z "$REMOTE_INFRA_HOST" ]]; then
        echo ""
        echo "==> Erro: modo remote-infra exige AXON_INFRA_HOST ou AXON_DESKTOP_HOST."
        exit 1
    fi
    echo ""
    echo "==> Modo infra remota: $REMOTE_INFRA_HOST"
    echo "    Docker local e pull de modelos serão ignorados."
    echo ""
    echo "==> Validando serviços remotos..."
    check_service "Qdrant"   "http://${REMOTE_INFRA_HOST}:6333/collections"
    check_service "Langfuse" "http://${REMOTE_INFRA_HOST}:3000"
    check_service "Ollama"   "http://${REMOTE_INFRA_HOST}:11434/api/tags"
elif [[ "$SETUP_MODE" == "minimal" ]]; then
    echo ""
    echo "==> Modo minimal selecionado"
    echo "    Docker local, validação remota e pull de modelos serão ignorados."
elif [[ "$PLAN_START_LOCAL_STACK" == "1" ]]; then
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
    IFS=',' read -r -a MODELS_TO_PULL <<< "$PLAN_PULL_MODELS"
    for model in "${MODELS_TO_PULL[@]}"; do
        [[ -n "$model" ]] || continue
        if [[ "$model" == "gemma4:26b" ]] && ! {
            { [[ "$PLATFORM" == "pc" ]] && [[ "${VRAM_GB:-0}" -ge 16 ]]; } ||
            { [[ "$PLATFORM" == "mac" ]] && [[ "${MEM_GB:-0}" -ge 32 ]]; }
        }; then
            echo "    Hardware insuficiente para gemma4:26b — usando gemma4:e4b como fallback"
            continue
        fi
        echo "    Puxando $model..."
        ollama pull "$model"
    done
else
    echo ""
    echo "==> Nenhuma ação de stack local foi planejada para este modo."
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
