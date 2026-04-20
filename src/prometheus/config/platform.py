import platform
import subprocess
from dataclasses import dataclass


@dataclass
class PlatformConfig:
    platform: str
    embedding_providers: list[str]
    ollama_flash: bool
    max_models: int
    model_primary: str
    model_knowledge: str
    keep_alive: str


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


def _to_dotenv(config: PlatformConfig) -> str:
    providers = ",".join(config.embedding_providers)
    return (
        f"PROMETHEUS_PLATFORM={config.platform}\n"
        f"EMBEDDING_PROVIDERS={providers}\n"
        f"OLLAMA_FLASH={str(config.ollama_flash).lower()}\n"
        f"OLLAMA_MAX_LOADED_MODELS={config.max_models}\n"
        f"OLLAMA_MODEL_PRIMARY={config.model_primary}\n"
        f"OLLAMA_MODEL_KNOWLEDGE={config.model_knowledge}\n"
        f"OLLAMA_KEEP_ALIVE={config.keep_alive}\n"
    )


if __name__ == "__main__":
    # Invocado pelo setup.sh para gerar .env.local
    config = detect_platform()
    print(_to_dotenv(config), end="")
