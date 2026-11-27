#!/usr/bin/env bash
set -euo pipefail

# setup.sh — bootstrap da infra AXON
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
    ollama_available=shutil.which("docker") is not None,
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
start_local_stack = "1" if plan.start_local_stack else "0"
validate_remote = "1" if plan.validate_remote_services else "0"

print(f"{start_local_stack}\t{validate_remote}")
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
IFS=$'\t' read -r PLAN_START_LOCAL_STACK PLAN_VALIDATE_REMOTE <<< "$(resolve_setup_plan "$SETUP_MODE" "${REMOTE_INFRA_HOST:--}")"

echo "==> Detectando plataforma..."

if [[ "$OSTYPE" == "darwin"* ]]; then
    MEM_BYTES=$(sysctl -n hw.memsize)
    MEM_GB=$((MEM_BYTES / 1024 / 1024 / 1024))
    echo "    Mac detectado — ${MEM_GB}GB memória unificada"
else
    if command -v nvidia-smi &>/dev/null; then
        if VRAM_MB=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -d ' '); then
            VRAM_GB=$((VRAM_MB / 1024))
            echo "    PC detectado — ${VRAM_GB}GB VRAM (NVIDIA)"
        else
            echo "    PC detectado — nvidia-smi falhou, usando CPU fallback"
        fi
    else
        echo "    PC detectado — nvidia-smi não encontrado, usando CPU fallback"
    fi
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
# Postgres is not HTTP; probe the port with bash's /dev/tcp (dec-121 backend).
check_tcp() {
    local name=$1
    local host=$2
    local port=$3
    local t=""
    command -v timeout &>/dev/null && t="timeout 3"  # absent on stock macOS
    if $t bash -c "exec 3<>/dev/tcp/$host/$port" &>/dev/null; then
        echo "    [OK] $name"
    else
        echo "    [WARN] $name não respondeu em $host:$port - pode ainda estar iniciando"
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
    echo "    Docker local será ignorado."
    echo ""
    echo "==> Validando serviços remotos..."
    check_tcp     "Postgres" "$REMOTE_INFRA_HOST" 5433
elif [[ "$SETUP_MODE" == "minimal" ]]; then
    echo ""
    echo "==> Modo minimal selecionado"
    echo "    Docker local será ignorado."
elif [[ "$PLAN_START_LOCAL_STACK" == "1" ]]; then
    echo ""
    echo "==> Subindo Postgres (docker compose up -d axon-postgres)"
    docker compose up -d axon-postgres

    echo ""
    echo "==> Aguardando serviços ficarem healthy..."
    sleep 5

    if docker compose exec -T axon-postgres pg_isready -U axon &>/dev/null; then
        echo "    [OK] Postgres"
    else
        echo "    [WARN] Postgres (axon-postgres) não respondeu - pode ainda estar iniciando"
    fi
else
    echo ""
    echo "==> Nenhuma ação de stack local foi planejada para este modo."
fi

echo ""
echo "==> Setup concluído."
echo "Optional TIL hook: install Ollama (https://ollama.com) and run: ollama pull gemma4:e4b"
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
