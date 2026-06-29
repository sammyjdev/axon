from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv

from axon.router.llm_backend import (
    default_compressor_model,
    default_scoring_model,
    resolve_litellm_model,
)

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

_BUILTIN_PROFILES: dict[str, dict[str, object]] = {
    "solo-dev": {
        "description": "Single developer default",
        "mode": "hybrid-local",
    },
    "team-dev": {
        "description": "Shared team setup",
        "mode": "remote-infra",
    },
    "privacy-first": {
        "description": "Prefer local or remote self-hosted paths",
        "mode": "minimal",
    },
}


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
    pg_url: str
    redis_url: str
    rtk_max_tokens: int
    caveman_num_ctx: int
    ollama_remote_host: str | None
    ollama_local_host: str
    caveman_model: str
    scoring_model: str
    scoring_num_ctx: int
    classifier_cloud_model: str
    classifier_timeout_seconds: float
    policy_version: str
    provider_anthropic_enabled: bool
    provider_openrouter_enabled: bool
    provider_ollama_enabled: bool
    provider_profile: str
    openrouter_compliance_required: bool
    expansion: ExpansionConfig
    active_profile: str | None = None
    vector_backend: str = "pgvector"
    fileindex_backend: str = "sqlite"
    graph_backend: str = "sqlite"
    decisions_backend: str = "sqlite"
    sessions_backend: str = "sqlite"

    @property
    def data_root(self) -> Path:
        return self.engine_root / "data"

    def vault_context_root(self, ctx: str) -> Path:
        return self.vault_root / ctx.strip().lower()


@dataclass(frozen=True)
class CapabilitySelection:
    enabled_features: tuple[str, ...]
    overkill_features: tuple[str, ...]


_WORK_CONTEXTS = {"work", "corporate"}


def _env_path(name: str, default: Path) -> Path:
    return Path(os.environ.get(name, str(default))).expanduser()


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


_VALID_VECTOR_BACKENDS = ("pgvector",)


def _resolve_concern_backend(concern: str, overrides: dict) -> str:
    """Select a relational backend for ``concern``.

    Precedence: ``AXON_<CONCERN>_BACKEND`` env > ``AXON_DB_BACKEND`` env (master
    switch) > per-concern toml > ``db_backend`` toml > default ``postgres``.
    """
    raw = (
        os.environ.get(f"AXON_{concern.upper()}_BACKEND")
        or os.environ.get("AXON_DB_BACKEND")
        or overrides.get(f"{concern}_backend")
        or overrides.get("db_backend")
        or "postgres"
    )
    backend = raw.strip().lower()
    if backend not in ("sqlite", "postgres"):
        raise ValueError(
            f"Invalid {concern}_backend {backend!r}; expected one of ['sqlite', 'postgres']"
        )
    return backend


def _resolve_fileindex_backend(overrides: dict) -> str:
    """Select the file_index backend: AXON_FILEINDEX_BACKEND env > axon.toml > default."""
    return _resolve_concern_backend("fileindex", overrides)


def _resolve_graph_backend(overrides: dict) -> str:
    """Select the graph backend: AXON_GRAPH_BACKEND env > axon.toml > default."""
    return _resolve_concern_backend("graph", overrides)


def _resolve_decisions_backend(overrides: dict) -> str:
    """Select the decisions backend: AXON_DECISIONS_BACKEND env > axon.toml > default."""
    return _resolve_concern_backend("decisions", overrides)


def _resolve_sessions_backend(overrides: dict) -> str:
    """Select the sessions backend: AXON_SESSIONS_BACKEND env > axon.toml > default."""
    return _resolve_concern_backend("sessions", overrides)


def _resolve_vector_backend(overrides: dict) -> str:
    """Select the vector backend: AXON_VECTOR_BACKEND env > axon.toml > default."""
    raw = os.environ.get("AXON_VECTOR_BACKEND") or overrides.get("vector_backend") or "pgvector"
    backend = raw.strip().lower()
    if backend not in _VALID_VECTOR_BACKENDS:
        raise ValueError(
            f"Invalid vector_backend {backend!r}; expected one of {list(_VALID_VECTOR_BACKENDS)}"
        )
    return backend


def _load_toml_runtime_overrides() -> dict[str, str]:
    config_path = get_axon_config_path()
    if not config_path.exists():
        return {}

    payload = tomllib.loads(config_path.read_text(encoding="utf-8"))
    runtime = payload.get("runtime")
    if not isinstance(runtime, dict):
        return {}
    allowed = {
        "mode", "engine_root", "vault_root", "active_profile", "vector_backend",
        "fileindex_backend", "graph_backend", "decisions_backend", "sessions_backend",
        "db_backend",
    }
    return {key: str(value) for key, value in runtime.items() if key in allowed}


def get_runtime_sources() -> dict[str, str]:
    overrides = _load_toml_runtime_overrides()
    return {
        "mode": (
            "env"
            if "AXON_RUNTIME_MODE" in os.environ
            else ("toml" if "mode" in overrides else "default")
        ),
        "engine_root": (
            "env"
            if "AXON_ENGINE" in os.environ
            else ("toml" if "engine_root" in overrides else "default")
        ),
        "vault_root": (
            "env"
            if "AXON_VAULT" in os.environ
            else ("toml" if "vault_root" in overrides else "default")
        ),
    }


def get_axon_config_path() -> Path:
    config_env = os.environ.get("AXON_CONFIG")
    if config_env:
        return Path(config_env).expanduser()
    new_path = Path.cwd() / "axon.toml"
    legacy_path = Path.cwd() / "prometheus.toml"
    # Compat: read legacy prometheus.toml if axon.toml ainda nao existe.
    # Migration: rename file localmente. Sem warning duro pra nao poluir CLI.
    if not new_path.exists() and legacy_path.exists():
        return legacy_path
    return new_path


def _load_toml_payload() -> dict:
    config_path = get_axon_config_path()
    if not config_path.exists():
        return {}
    return tomllib.loads(config_path.read_text(encoding="utf-8"))


def _coerce_profile_features(value: object) -> tuple[str, ...]:
    if isinstance(value, list | tuple):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return ()


def _parse_profile(
    name: str,
    profile: dict[str, object],
) -> dict[str, str | tuple[str, ...] | None]:
    return {
        "name": name,
        "description": str(profile.get("description", "")),
        "mode": str(profile.get("mode", "")),
        "cloud_policy": (
            str(profile["cloud_policy"]).strip()
            if profile.get("cloud_policy") is not None
            else None
        ),
        "infra_strategy": (
            str(profile["infra_strategy"]).strip()
            if profile.get("infra_strategy") is not None
            else None
        ),
        "memory_tier": (
            str(profile["memory_tier"]).strip() if profile.get("memory_tier") is not None else None
        ),
        "enabled_features": _coerce_profile_features(profile.get("enabled_features")),
    }


def _format_toml_string(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _profile_toml_lines(
    profile: dict[str, str | tuple[str, ...] | None],
) -> list[str]:
    lines = [
        f"[profiles.{profile['name']}]",
        f"description = {_format_toml_string(str(profile['description']))}",
        f"mode = {_format_toml_string(str(profile['mode']))}",
    ]
    for key in ("cloud_policy", "infra_strategy", "memory_tier"):
        value = profile.get(key)
        if value:
            lines.append(f"{key} = {_format_toml_string(str(value))}")
    enabled_features = profile.get("enabled_features")
    if enabled_features:
        rendered = ", ".join(_format_toml_string(feature) for feature in enabled_features)
        lines.append(f"enabled_features = [{rendered}]")
    return lines


def list_profiles() -> list[tuple[str, str, str]]:
    payload = _load_toml_payload()
    raw_profiles = payload.get("profiles")
    if isinstance(raw_profiles, dict):
        profiles = {
            str(name): dict(profile)
            for name, profile in raw_profiles.items()
            if isinstance(profile, dict)
        }
    else:
        profiles = {name: dict(profile) for name, profile in _BUILTIN_PROFILES.items()}
    result: list[tuple[str, str, str]] = []
    for name in sorted(profiles):
        profile = profiles.get(name)
        if not isinstance(profile, dict):
            continue
        parsed = _parse_profile(name, profile)
        description = str(parsed["description"])
        mode = str(parsed["mode"])
        result.append((name, description, mode))
    return result


def get_active_profile() -> str | None:
    overrides = _load_toml_runtime_overrides()
    return overrides.get("active_profile")


def get_profile(name: str) -> dict[str, str | tuple[str, ...] | None]:
    payload = _load_toml_payload()
    profiles = _merged_profiles(payload)
    if name not in profiles:
        raise ValueError(f"Unknown profile: {name}")
    profile = profiles[name]
    if not isinstance(profile, dict):
        raise ValueError(f"Invalid profile: {name}")
    return _parse_profile(name, profile)


def select_capabilities(
    *,
    profile: dict[str, str | tuple[str, ...] | None] | None = None,
    use_case: str | None = None,
    privacy: str | None = None,
    hardware: str | None = None,
    preferred_mode: str | None = None,
    infra: str | None = None,
    memory: str | None = None,
    cloud: str | None = None,
) -> CapabilitySelection:
    if profile is not None:
        mode = str(profile.get("mode", "")).strip().lower()
        cloud_policy = _normalize_optional_value(profile.get("cloud_policy"))
        infra_strategy = _normalize_optional_value(profile.get("infra_strategy"))
        memory_tier = _normalize_optional_value(profile.get("memory_tier"))
        explicit_features = _coerce_profile_features(profile.get("enabled_features"))
        normalized_hardware = None
    else:
        if not use_case or not privacy or not hardware:
            raise ValueError("select_capabilities requires a profile or recommendation inputs")
        _profile_name, mode = recommend_profile(
            use_case=use_case,
            privacy=privacy,
            hardware=hardware,
            preferred_mode=preferred_mode,
            infra=infra,
            memory=memory,
            cloud=cloud,
        )
        cloud_policy = _normalize_optional_value(cloud)
        infra_strategy = _normalize_optional_value(infra)
        memory_tier = _normalize_optional_value(memory)
        explicit_features = ()
        normalized_hardware = hardware.strip().lower()

    enabled = set(explicit_features)
    overkill: set[str] = set()

    if mode == "minimal" or memory_tier == "light":
        enabled.add("lean-context")
        overkill.update({"heavy-local-models", "shared-remote-infra"})

    if mode == "remote-infra" or infra_strategy == "remote":
        enabled.add("shared-remote-infra")
        overkill.update({"heavy-local-models", "offline-first"})

    if mode == "full-local":
        enabled.update({"local-rag", "offline-first"})
        overkill.add("shared-remote-infra")

    if cloud_policy == "deny":
        enabled.add("local-rag")
        overkill.add("cloud-routing")

    if (
        normalized_hardware in {"nvidia", "linux-workstation", "high-capability"}
        and mode == "full-local"
    ):
        enabled.add("heavy-local-models")

    return CapabilitySelection(
        enabled_features=tuple(sorted(enabled)),
        overkill_features=tuple(sorted(overkill - enabled)),
    )


def recommend_profile(
    *,
    use_case: str,
    privacy: str,
    hardware: str,
    preferred_mode: str | None = None,
    infra: str | None = None,
    memory: str | None = None,
    cloud: str | None = None,
) -> tuple[str, str]:
    normalized_use_case = use_case.strip().lower()
    normalized_privacy = privacy.strip().lower()
    normalized_hardware = hardware.strip().lower()
    normalized_preferred_mode = preferred_mode.strip().lower() if preferred_mode else None
    normalized_infra = infra.strip().lower() if infra else None
    normalized_memory = memory.strip().lower() if memory else None
    normalized_cloud = cloud.strip().lower() if cloud else None

    if normalized_preferred_mode in _RUNTIME_MODES:
        return _profile_for_mode(normalized_preferred_mode), normalized_preferred_mode
    if normalized_infra == "remote":
        return "team-dev", "remote-infra"
    if normalized_memory == "light":
        return "privacy-first", "minimal"
    if normalized_cloud == "deny" and normalized_privacy in {"internal", "public"}:
        return "privacy-first", "minimal"

    if normalized_privacy in {"restricted", "confidential"}:
        return "privacy-first", "minimal"
    if normalized_use_case in {"team", "shared", "corporate"}:
        return "team-dev", "remote-infra"
    if normalized_hardware in {"nvidia", "linux-workstation"}:
        return "solo-dev", "hybrid-local"
    return "solo-dev", "hybrid-local"


def _normalize_optional_value(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    return normalized or None


def _profile_for_mode(mode: str) -> str:
    if mode == "remote-infra":
        return "team-dev"
    if mode == "minimal":
        return "privacy-first"
    return "solo-dev"


def use_profile(name: str) -> None:
    payload = _load_toml_payload()
    profiles = _merged_profiles(payload)
    if name not in profiles:
        raise ValueError(f"Unknown profile: {name}")
    profile = profiles[name]
    if not isinstance(profile, dict):
        raise ValueError(f"Invalid profile: {name}")
    mode = str(profile.get("mode", "")).strip().lower()
    if mode not in _RUNTIME_MODES:
        raise ValueError(f"Profile {name!r} has invalid mode {mode!r}")

    config_path = get_axon_config_path()
    lines = config_path.read_text(encoding="utf-8").splitlines()
    lines = _ensure_builtin_profile_lines(lines)
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
    _sync_env_runtime_mode(config_path.parent / ".env.local", mode)


def _merged_profiles(payload: dict[str, object]) -> dict[str, dict[str, object]]:
    merged = {name: dict(profile) for name, profile in _BUILTIN_PROFILES.items()}
    raw_profiles = payload.get("profiles")
    if not isinstance(raw_profiles, dict):
        return merged
    for name, profile in raw_profiles.items():
        if isinstance(profile, dict):
            merged[str(name)] = dict(profile)
    return merged


def _ensure_builtin_profile_lines(lines: list[str]) -> list[str]:
    payload = tomllib.loads("\n".join(lines) + "\n") if lines else {}
    profiles = payload.get("profiles")
    existing = set(profiles.keys()) if isinstance(profiles, dict) else set()
    missing = [name for name in _BUILTIN_PROFILES if name not in existing]
    if not missing:
        return lines

    updated = list(lines)
    if updated and updated[-1].strip():
        updated.append("")
    for name in missing:
        updated.extend(_profile_toml_lines(_parse_profile(name, _BUILTIN_PROFILES[name])))
        updated.append("")
    return updated


def create_profile(
    name: str,
    *,
    description: str,
    mode: str,
    cloud_policy: str | None = None,
    infra_strategy: str | None = None,
    memory_tier: str | None = None,
    enabled_features: tuple[str, ...] = (),
) -> None:
    normalized_mode = mode.strip().lower()
    if normalized_mode not in _RUNTIME_MODES:
        raise ValueError(f"Invalid mode: {mode}")
    config_path = get_axon_config_path()
    payload = _load_toml_payload()
    profiles = payload.get("profiles")
    if isinstance(profiles, dict) and name in profiles:
        raise ValueError(f"Profile already exists: {name}")
    lines = config_path.read_text(encoding="utf-8").splitlines()
    profile = {
        "name": name,
        "description": description,
        "mode": normalized_mode,
        "cloud_policy": cloud_policy.strip().lower() if cloud_policy else None,
        "infra_strategy": infra_strategy.strip().lower() if infra_strategy else None,
        "memory_tier": memory_tier.strip().lower() if memory_tier else None,
        "enabled_features": tuple(
            feature.strip()
            for feature in enabled_features
            if feature.strip()
        ),
    }
    lines.extend(
        [
            "",
            *_profile_toml_lines(profile),
        ]
    )
    config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def export_profile(name: str) -> str:
    profile = get_profile(name)
    return "\n".join([*_profile_toml_lines(profile), ""])


def _sync_env_runtime_mode(env_path: Path, mode: str) -> None:
    if not env_path.exists():
        return
    lines = env_path.read_text(encoding="utf-8").splitlines()
    updated: list[str] = []
    replaced = False
    for line in lines:
        if line.startswith("AXON_RUNTIME_MODE="):
            updated.append(f"AXON_RUNTIME_MODE={mode}")
            replaced = True
        else:
            updated.append(line)
    if not replaced:
        updated.append(f"AXON_RUNTIME_MODE={mode}")
    env_path.write_text("\n".join(updated) + "\n", encoding="utf-8")


def _load_runtime_mode() -> RuntimeMode:
    overrides = _load_toml_runtime_overrides()
    value = (
        os.environ.get("AXON_RUNTIME_MODE", overrides.get("mode", "full-local"))
        .strip()
        .lower()
    )
    if value not in _RUNTIME_MODES:
        supported = ", ".join(_RUNTIME_MODES)
        raise ValueError(
            f"Invalid AXON_RUNTIME_MODE={value!r}. Supported modes: {supported}."
        )
    return value


def _load_expansion_config(engine_root: Path) -> ExpansionConfig:
    expansion_root = _env_path("AXON_EXPANSION_ROOT", engine_root / "data" / "expansion")
    default_contexts = tuple(
        part.strip().lower()
        for part in os.environ.get(
            "AXON_EXPANSION_CONTEXTS",
            "knowledge,career,personal",
        ).split(",")
        if part.strip()
    )
    paths = ExpansionPaths(
        root=expansion_root,
        staging_root=_env_path("AXON_EXPANSION_STAGING_ROOT", expansion_root / "staging"),
        telemetry_root=_env_path(
            "AXON_EXPANSION_TELEMETRY_ROOT",
            expansion_root / "telemetry",
        ),
        budget_root=_env_path("AXON_EXPANSION_BUDGET_ROOT", expansion_root / "budget"),
    )
    budget = ExpansionBudgetConfig(
        monthly_budget_usd=float(os.environ.get("AXON_EXPANSION_MONTHLY_BUDGET", "4.0")),
        soft_cap_usd=float(os.environ.get("AXON_EXPANSION_SOFT_CAP", "3.2")),
        hard_cap_usd=float(os.environ.get("AXON_EXPANSION_HARD_CAP", "4.0")),
    )
    return ExpansionConfig(
        enabled=_env_bool("AXON_EXPANSION_ENABLED", True),
        manual_trigger_only=_env_bool("AXON_EXPANSION_MANUAL_ONLY", True),
        default_contexts=default_contexts,
        allow_cloud_research=_env_bool("AXON_EXPANSION_ALLOW_CLOUD", True),
        source_catalog_path=_env_path(
            "AXON_EXPANSION_SOURCE_CATALOG",
            engine_root / "config" / "expansion_sources.json",
        ),
        paths=paths,
        budget=budget,
    )


def load_runtime_config() -> RuntimeConfig:
    overrides = _load_toml_runtime_overrides()
    engine_root = _env_path(
        "AXON_ENGINE",
        Path(overrides.get("engine_root", str(Path.home() / "dev/axon"))),
    )
    vault_root = _env_path(
        "AXON_VAULT",
        Path(overrides.get("vault_root", str(Path.home() / "vault"))),
    )
    return RuntimeConfig(
        mode=_load_runtime_mode(),
        active_profile=overrides.get("active_profile"),
        engine_root=engine_root,
        vault_root=vault_root,
        db_path=engine_root / "data" / "axon.db",
        pg_url=os.environ.get("AXON_PG_URL", "postgresql://axon:axon@localhost:5433/axon"),
        redis_url=os.environ.get("REDIS_URL", "redis://localhost:6379"),
        rtk_max_tokens=int(os.environ.get("AXON_RTK_MAX_TOKENS", "450")),
        caveman_num_ctx=int(os.environ.get("AXON_CAVEMAN_NUM_CTX", "4096")),
        ollama_remote_host=os.environ.get("AXON_OLLAMA_REMOTE_HOST") or None,
        ollama_local_host=os.environ.get("AXON_OLLAMA_LOCAL_HOST", "http://127.0.0.1:11434"),
        caveman_model=os.environ.get(
            "AXON_CAVEMAN_MODEL",
            os.environ.get("OLLAMA_MODEL_PRIMARY", default_compressor_model()),
        ),
        scoring_model=resolve_litellm_model(
            os.environ.get("AXON_SCORING_MODEL", default_scoring_model())
        ),
        scoring_num_ctx=int(os.environ.get("AXON_SCORING_NUM_CTX", "8192")),
        classifier_cloud_model=_resolve_classifier_model(),
        classifier_timeout_seconds=float(os.environ.get("AXON_CLASSIFIER_TIMEOUT", "4.0")),
        policy_version=os.environ.get("AXON_POLICY_VERSION", "2026-04-21"),
        provider_anthropic_enabled=os.environ.get("AXON_PROVIDER_ANTHROPIC", "1") == "1",
        provider_openrouter_enabled=os.environ.get("AXON_PROVIDER_OPENROUTER", "1") == "1",
        provider_ollama_enabled=os.environ.get("AXON_PROVIDER_OLLAMA", "0") == "1",
        provider_profile=_resolve_provider_profile(),
        openrouter_compliance_required=_env_bool("AXON_OPENROUTER_COMPLIANCE", False),
        expansion=_load_expansion_config(engine_root),
        vector_backend=_resolve_vector_backend(overrides),
        fileindex_backend=_resolve_fileindex_backend(overrides),
        graph_backend=_resolve_graph_backend(overrides),
        decisions_backend=_resolve_decisions_backend(overrides),
        sessions_backend=_resolve_sessions_backend(overrides),
    )


def _resolve_provider_profile() -> str:
    from axon.router.profiles import available_profiles

    raw = os.environ.get("AXON_PROVIDER_PROFILE", "free").strip().lower()
    if raw not in available_profiles():
        raise ValueError(
            f"AXON_PROVIDER_PROFILE invalido: {raw!r}. Disponiveis: {available_profiles()}"
        )
    return raw


def _resolve_classifier_model() -> str:
    from axon.router.profiles import get_profile

    override = os.environ.get("AXON_CLASSIFIER_CLOUD_MODEL")
    if override:
        return override
    profile = get_profile(_resolve_provider_profile())
    return profile.classifier_model


def is_corporate_context(ctx: str | None) -> bool:
    if not ctx:
        return False
    return ctx.strip().lower() in _WORK_CONTEXTS
