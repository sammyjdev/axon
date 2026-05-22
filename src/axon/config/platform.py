import platform
import subprocess
import sys
from dataclasses import dataclass
from os import environ
from pathlib import Path

from axon.config.runtime import RuntimeConfig, RuntimeMode


@dataclass
class PlatformConfig:
    platform: str
    embedding_providers: list[str]
    ollama_flash: bool
    max_models: int
    model_primary: str
    model_knowledge: str
    keep_alive: str


@dataclass(frozen=True)
class DoctorReport:
    platform: str
    recommended_mode: str
    checks: dict[str, str]
    notes: list[str]
    sources: dict[str, str] | None = None
    configured_mode: str | None = None
    active_profile: str | None = None
    profile_mode: str | None = None


@dataclass(frozen=True)
class SetupPlan:
    runtime_mode: RuntimeMode
    compose_profile: str | None
    start_local_stack: bool
    validate_remote_services: bool
    pull_models: tuple[str, ...]


def detect_platform() -> PlatformConfig:
    system = platform.system()

    if system == "Darwin":
        mem_gb = _get_mac_memory()
        return PlatformConfig(
            platform="mac",
            embedding_providers=["CoreMLExecutionProvider", "CPUExecutionProvider"],
            ollama_flash=False,
            max_models=2 if mem_gb >= 32 else 1,
            model_primary="gemma4:e4b",
            model_knowledge="gemma4:26b" if mem_gb >= 24 else "gemma4:e4b",
            keep_alive="10m",
        )
    else:
        vram_gb = _get_nvidia_vram()
        return PlatformConfig(
            platform="pc",
            embedding_providers=["CUDAExecutionProvider"],
            ollama_flash=True,
            max_models=2 if vram_gb >= 16 else 1,
            model_primary="gemma4:e4b",
            model_knowledge="gemma4:26b" if vram_gb >= 10 else "gemma4:e4b",
            keep_alive="-1",
        )


def _get_mac_memory() -> int:
    result = subprocess.run(
        ["sysctl", "hw.memsize"],
        capture_output=True,
        text=True,
        check=True,
    )
    return int(result.stdout.split(":")[1].strip()) // (1024**3)


def _get_nvidia_vram() -> int:
    result = subprocess.run(
        ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        # nvidia-smi ausente — assume VRAM insuficiente para gemma4:26b
        return 0
    return int(result.stdout.strip()) // 1024


def build_doctor_report(
    runtime: RuntimeConfig,
    platform_config: PlatformConfig,
    *,
    docker_available: bool,
    ollama_available: bool,
    profile_mode: str | None = None,
    sources: dict[str, str] | None = None,
) -> DoctorReport:
    checks = {
        "engine_root": "ok" if runtime.engine_root.exists() else "missing",
        "vault_root": "ok" if runtime.vault_root.exists() else "missing",
        "docker": "ok" if docker_available else "missing",
        "ollama": "ok" if ollama_available else "missing",
        "remote_infra": "configured" if runtime.ollama_remote_host else "local",
    }
    recommended_mode = _recommend_operating_mode(
        runtime,
        platform_config,
        docker_available=docker_available,
        ollama_available=ollama_available,
    )
    notes: list[str] = []
    if runtime.active_profile and profile_mode and profile_mode != runtime.mode:
        notes.append(
            f"Profile '{runtime.active_profile}' expects mode '{profile_mode}',"
            f" but runtime is '{runtime.mode}'."
        )
    if runtime.mode != recommended_mode:
        notes.append(
            f"Current mode '{runtime.mode}' differs from recommended '{recommended_mode}'."
        )
    if recommended_mode == "minimal":
        notes.append("Local tooling incomplete or undersized; start with the smallest stack.")
    elif recommended_mode == "remote-infra":
        notes.append("Remote infra configured; keep heavy services off the current machine.")
    elif recommended_mode == "hybrid-local":
        notes.append("Prefer local workflow with a lighter infra footprint on this machine.")
    else:
        notes.append("GPU-capable local stack available.")
    return DoctorReport(
        platform=platform_config.platform,
        recommended_mode=recommended_mode,
        checks=checks,
        notes=notes,
        sources=sources or {},
        configured_mode=runtime.mode,
        active_profile=runtime.active_profile,
        profile_mode=profile_mode,
    )


def _recommend_operating_mode(
    runtime: RuntimeConfig,
    platform_config: PlatformConfig,
    *,
    docker_available: bool,
    ollama_available: bool,
) -> str:
    if runtime.ollama_remote_host:
        return "remote-infra"
    if not docker_available and not ollama_available:
        return "minimal"
    if platform_config.platform == "mac":
        return "hybrid-local"
    has_gpu = "CUDAExecutionProvider" in platform_config.embedding_providers
    if has_gpu and platform_config.max_models >= 2 and docker_available and ollama_available:
        return "full-local"
    if docker_available or ollama_available:
        return "hybrid-local"
    return "minimal"


def build_setup_plan(
    *,
    runtime_mode: RuntimeMode,
    platform_config: PlatformConfig,
    remote_infra_host: str | None,
) -> SetupPlan:
    if runtime_mode == "remote-infra":
        return SetupPlan(
            runtime_mode=runtime_mode,
            compose_profile=None,
            start_local_stack=False,
            validate_remote_services=bool(remote_infra_host),
            pull_models=(),
        )
    if runtime_mode == "minimal":
        return SetupPlan(
            runtime_mode=runtime_mode,
            compose_profile=None,
            start_local_stack=False,
            validate_remote_services=False,
            pull_models=(),
        )

    base_models: list[str] = ["phi3:mini", platform_config.model_primary]
    if runtime_mode == "full-local" and platform_config.max_models >= 2:
        knowledge_model = platform_config.model_knowledge
        if knowledge_model not in base_models:
            base_models.append(knowledge_model)

    return SetupPlan(
        runtime_mode=runtime_mode,
        compose_profile="gpu"
        if runtime_mode == "full-local"
        and "CUDAExecutionProvider" in platform_config.embedding_providers
        else "cpu",
        start_local_stack=True,
        validate_remote_services=False,
        pull_models=tuple(base_models),
    )


def merge_env_text(source_text: str, target_text: str, *, mode: str = "replace") -> str:
    source_lines = source_text.splitlines()
    target_lines = target_text.splitlines()

    target_index: dict[str, int] = {}
    for idx, line in enumerate(target_lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            continue
        key = line.split("=", 1)[0]
        target_index[key] = idx

    for line in source_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            continue
        key = line.split("=", 1)[0]
        if key in target_index:
            if mode == "replace":
                target_lines[target_index[key]] = line
        else:
            target_lines.append(line)

    return "\n".join(target_lines) + "\n"


def merge_env_files(source_path: Path, target_path: Path, *, mode: str = "replace") -> None:
    source_text = source_path.read_text() if source_path.exists() else ""
    target_text = target_path.read_text() if target_path.exists() else ""
    target_path.write_text(merge_env_text(source_text, target_text, mode=mode))


def _to_dotenv(config: PlatformConfig) -> str:
    providers = ",".join(config.embedding_providers)
    remote_host = environ.get("AXON_INFRA_HOST") or environ.get("AXON_DESKTOP_HOST")
    env_text = (
        f"AXON_PLATFORM={config.platform}\n"
        f"EMBEDDING_PROVIDERS={providers}\n"
        f"OLLAMA_FLASH={str(config.ollama_flash).lower()}\n"
        f"OLLAMA_MAX_LOADED_MODELS={config.max_models}\n"
        f"OLLAMA_MODEL_PRIMARY={config.model_primary}\n"
        f"OLLAMA_MODEL_KNOWLEDGE={config.model_knowledge}\n"
        f"OLLAMA_KEEP_ALIVE={config.keep_alive}\n"
    )
    if remote_host:
        env_text += (
            f"AXON_INFRA_HOST={remote_host}\n"
            f"QDRANT_URL=http://{remote_host}:6333\n"
            f"REDIS_URL=redis://{remote_host}:6379\n"
            f"NEO4J_URI=bolt://{remote_host}:7687\n"
            f"LANGFUSE_HOST=http://{remote_host}:3000\n"
            f"AXON_OLLAMA_LOCAL_HOST=http://{remote_host}:11434\n"
            f"AXON_OLLAMA_REMOTE_HOST=http://{remote_host}:11434\n"
            f"OLLAMA_HOST=http://{remote_host}:11434\n"
        )
    return env_text

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--merge-env":
        if len(sys.argv) != 5:
            raise SystemExit("usage: platform.py --merge-env <source> <target> <mode>")
        merge_env_files(Path(sys.argv[2]), Path(sys.argv[3]), mode=sys.argv[4])
    elif len(sys.argv) > 1 and sys.argv[1] == "--setup-plan":
        if len(sys.argv) != 4:
            raise SystemExit(
                "usage: platform.py --setup-plan <runtime-mode> <remote-infra-host-or-dash>"
            )
        mode = sys.argv[2].strip().lower()
        if mode not in {"full-local", "hybrid-local", "remote-infra", "minimal"}:
            raise SystemExit(f"invalid runtime mode: {mode}")
        remote_host = None if sys.argv[3] == "-" else sys.argv[3]
        plan = build_setup_plan(
            runtime_mode=mode,  # type: ignore[arg-type]
            platform_config=detect_platform(),
            remote_infra_host=remote_host,
        )
        print(plan.compose_profile or "-", end="\t")
        print("1" if plan.start_local_stack else "0", end="\t")
        print("1" if plan.validate_remote_services else "0", end="\t")
        print(",".join(plan.pull_models))
    else:
        # Invocado pelo setup.sh para gerar .env.local
        config = detect_platform()
        print(_to_dotenv(config), end="")
