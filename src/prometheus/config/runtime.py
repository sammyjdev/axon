from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv

# Carrega .env.local sobre .env, sem sobrescrever vars já exportadas pelo shell
load_dotenv(Path(__file__).parents[3] / ".env", override=False)
load_dotenv(Path(__file__).parents[3] / ".env.local", override=False)

RuntimeMode = Literal["full-local", "hybrid-local", "remote-infra", "minimal"]
_RUNTIME_MODES: tuple[RuntimeMode, ...] = (
    "full-local",
    "hybrid-local",
    "remote-infra",
    "minimal",
)


@dataclass(frozen=True)
class ExpansionPaths:
    root: Path
    staging_root: Path
    telemetry_root: Path
    budget_root: Path

    def staging_context_root(self, ctx: str) -> Path:
        return self.staging_root / ctx.strip().lower()

    def monthly_budget_file(self, for_date: date | None = None) -> Path:
        current = for_date or date.today()
        return self.budget_root / f"{current:%Y-%m}.json"

    @property
    def execution_telemetry_file(self) -> Path:
        return self.telemetry_root / "executions.jsonl"


@dataclass(frozen=True)
class ExpansionBudgetConfig:
    monthly_budget_usd: float
    soft_cap_usd: float
    hard_cap_usd: float


@dataclass(frozen=True)
class ExpansionConfig:
    enabled: bool
    manual_trigger_only: bool
    default_contexts: tuple[str, ...]
    allow_cloud_research: bool
    source_catalog_path: Path
    paths: ExpansionPaths
    budget: ExpansionBudgetConfig


@dataclass(frozen=True)
class RuntimeConfig:
    mode: RuntimeMode
    engine_root: Path
    vault_root: Path
    db_path: Path
    qdrant_url: str
    redis_url: str
    rtk_max_tokens: int
    caveman_num_ctx: int
    ollama_remote_host: str | None
    ollama_local_host: str
    caveman_model: str
    classifier_cloud_model: str
    classifier_timeout_seconds: float
    policy_version: str
    provider_anthropic_enabled: bool
    provider_openrouter_enabled: bool
    provider_ollama_enabled: bool
    expansion: ExpansionConfig
    active_profile: str | None = None

    @property
    def data_root(self) -> Path:
        return self.engine_root / "data"

    def vault_context_root(self, ctx: str) -> Path:
        return self.vault_root / ctx.strip().lower()


_WORK_CONTEXTS = {"work", "corporate"}


def _env_path(name: str, default: Path) -> Path:
    return Path(os.environ.get(name, str(default))).expanduser()


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _load_toml_runtime_overrides() -> dict[str, str]:
    config_path = get_prometheus_config_path()
    if not config_path.exists():
        return {}

    payload = tomllib.loads(config_path.read_text(encoding="utf-8"))
    runtime = payload.get("runtime")
    if not isinstance(runtime, dict):
        return {}
    return {
        key: str(value)
        for key, value in runtime.items()
        if key in {"mode", "engine_root", "vault_root", "active_profile"}
    }


def get_prometheus_config_path() -> Path:
    config_env = os.environ.get("PROMETHEUS_CONFIG")
    return Path(config_env).expanduser() if config_env else Path.cwd() / "prometheus.toml"


def _load_toml_payload() -> dict:
    config_path = get_prometheus_config_path()
    if not config_path.exists():
        return {}
    return tomllib.loads(config_path.read_text(encoding="utf-8"))


def list_profiles() -> list[tuple[str, str, str]]:
    payload = _load_toml_payload()
    profiles = payload.get("profiles")
    if not isinstance(profiles, dict):
        return []
    result: list[tuple[str, str, str]] = []
    for name in sorted(profiles):
        profile = profiles.get(name)
        if not isinstance(profile, dict):
            continue
        description = str(profile.get("description", ""))
        mode = str(profile.get("mode", ""))
        result.append((name, description, mode))
    return result


def get_active_profile() -> str | None:
    overrides = _load_toml_runtime_overrides()
    return overrides.get("active_profile")


def use_profile(name: str) -> None:
    payload = _load_toml_payload()
    profiles = payload.get("profiles")
    if not isinstance(profiles, dict) or name not in profiles:
        raise ValueError(f"Unknown profile: {name}")
    profile = profiles[name]
    if not isinstance(profile, dict):
        raise ValueError(f"Invalid profile: {name}")
    mode = str(profile.get("mode", "")).strip().lower()
    if mode not in _RUNTIME_MODES:
        raise ValueError(f"Profile {name!r} has invalid mode {mode!r}")

    config_path = get_prometheus_config_path()
    lines = config_path.read_text(encoding="utf-8").splitlines()
    in_runtime = False
    saw_mode = False
    saw_active = False
    updated: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if in_runtime and not saw_active:
                updated.append(f'active_profile = "{name}"')
                saw_active = True
            if in_runtime and not saw_mode:
                updated.append(f'mode = "{mode}"')
                saw_mode = True
            in_runtime = stripped == "[runtime]"
            updated.append(line)
            continue
        if in_runtime and stripped.startswith("mode = "):
            updated.append(f'mode = "{mode}"')
            saw_mode = True
            continue
        if in_runtime and stripped.startswith("active_profile = "):
            updated.append(f'active_profile = "{name}"')
            saw_active = True
            continue
        updated.append(line)

    if in_runtime and not saw_active:
        updated.append(f'active_profile = "{name}"')
    if in_runtime and not saw_mode:
        updated.append(f'mode = "{mode}"')
    config_path.write_text("\n".join(updated) + "\n", encoding="utf-8")


def _load_runtime_mode() -> RuntimeMode:
    overrides = _load_toml_runtime_overrides()
    value = os.environ.get("PROMETHEUS_RUNTIME_MODE", overrides.get("mode", "full-local")).strip().lower()
    if value not in _RUNTIME_MODES:
        supported = ", ".join(_RUNTIME_MODES)
        raise ValueError(
            f"Invalid PROMETHEUS_RUNTIME_MODE={value!r}. Supported modes: {supported}."
        )
    return value


def _load_expansion_config(engine_root: Path) -> ExpansionConfig:
    expansion_root = _env_path("PROMETHEUS_EXPANSION_ROOT", engine_root / "data" / "expansion")
    default_contexts = tuple(
        part.strip().lower()
        for part in os.environ.get(
            "PROMETHEUS_EXPANSION_CONTEXTS",
            "knowledge,career,personal",
        ).split(",")
        if part.strip()
    )
    paths = ExpansionPaths(
        root=expansion_root,
        staging_root=_env_path("PROMETHEUS_EXPANSION_STAGING_ROOT", expansion_root / "staging"),
        telemetry_root=_env_path(
            "PROMETHEUS_EXPANSION_TELEMETRY_ROOT",
            expansion_root / "telemetry",
        ),
        budget_root=_env_path("PROMETHEUS_EXPANSION_BUDGET_ROOT", expansion_root / "budget"),
    )
    budget = ExpansionBudgetConfig(
        monthly_budget_usd=float(os.environ.get("PROMETHEUS_EXPANSION_MONTHLY_BUDGET", "4.0")),
        soft_cap_usd=float(os.environ.get("PROMETHEUS_EXPANSION_SOFT_CAP", "3.2")),
        hard_cap_usd=float(os.environ.get("PROMETHEUS_EXPANSION_HARD_CAP", "4.0")),
    )
    return ExpansionConfig(
        enabled=_env_bool("PROMETHEUS_EXPANSION_ENABLED", True),
        manual_trigger_only=_env_bool("PROMETHEUS_EXPANSION_MANUAL_ONLY", True),
        default_contexts=default_contexts,
        allow_cloud_research=_env_bool("PROMETHEUS_EXPANSION_ALLOW_CLOUD", True),
        source_catalog_path=_env_path(
            "PROMETHEUS_EXPANSION_SOURCE_CATALOG",
            engine_root / "config" / "expansion_sources.json",
        ),
        paths=paths,
        budget=budget,
    )


def load_runtime_config() -> RuntimeConfig:
    overrides = _load_toml_runtime_overrides()
    engine_root = _env_path(
        "PROMETHEUS_ENGINE",
        Path(overrides.get("engine_root", str(Path.home() / "dev/Prometheus"))),
    )
    vault_root = _env_path(
        "PROMETHEUS_VAULT",
        Path(overrides.get("vault_root", str(Path.home() / "vault"))),
    )
    return RuntimeConfig(
        mode=_load_runtime_mode(),
        active_profile=overrides.get("active_profile"),
        engine_root=engine_root,
        vault_root=vault_root,
        db_path=engine_root / "data" / "prometheus.db",
        qdrant_url=os.environ.get("QDRANT_URL", "http://localhost:6333"),
        redis_url=os.environ.get("REDIS_URL", "redis://localhost:6379"),
        rtk_max_tokens=int(os.environ.get("PROMETHEUS_RTK_MAX_TOKENS", "450")),
        caveman_num_ctx=int(os.environ.get("PROMETHEUS_CAVEMAN_NUM_CTX", "4096")),
        ollama_remote_host=os.environ.get("PROMETHEUS_OLLAMA_REMOTE_HOST") or None,
        ollama_local_host=os.environ.get("PROMETHEUS_OLLAMA_LOCAL_HOST", "http://127.0.0.1:11434"),
        caveman_model=os.environ.get(
            "PROMETHEUS_CAVEMAN_MODEL",
            os.environ.get("OLLAMA_MODEL_PRIMARY", "phi3:mini"),
        ),
        classifier_cloud_model=os.environ.get(
            "PROMETHEUS_CLASSIFIER_CLOUD_MODEL", "claude-haiku-4-5-20251001"
        ),
        classifier_timeout_seconds=float(os.environ.get("PROMETHEUS_CLASSIFIER_TIMEOUT", "4.0")),
        policy_version=os.environ.get("PROMETHEUS_POLICY_VERSION", "2026-04-21"),
        provider_anthropic_enabled=os.environ.get("PROMETHEUS_PROVIDER_ANTHROPIC", "1") == "1",
        provider_openrouter_enabled=os.environ.get("PROMETHEUS_PROVIDER_OPENROUTER", "1") == "1",
        provider_ollama_enabled=os.environ.get("PROMETHEUS_PROVIDER_OLLAMA", "1") == "1",
        expansion=_load_expansion_config(engine_root),
    )


def is_corporate_context(ctx: str | None) -> bool:
    if not ctx:
        return False
    return ctx.strip().lower() in _WORK_CONTEXTS
