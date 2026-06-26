"""Provider-aware litellm kwargs for the local roles (scoring, compressor).

A role's model is a full litellm id: ``ollama/phi3:mini`` (local) or
``groq/openai/gpt-oss-120b`` / ``cerebras/gpt-oss-120b`` (hosted). Only the
ollama path needs ``api_base`` (the endpoint) and ``num_ctx`` (KV-cache size,
which otherwise inherits the host's huge default and OOMs). Hosted providers
take neither.
"""

from __future__ import annotations

# dec-122: hosted backends for the local roles (scoring, compressor). This is a
# code flag, not an env var, so the decision holds without managing shell/.env
# profiles (provider API keys still come from the environment). When True the
# flag WINS over the per-role env vars — the whole point is to not depend on the
# generated .env.local. Flip to False to fall back to env / local Ollama models.
USE_HOSTED_LOCAL_ROLES = True

_HOSTED_SCORING_MODEL = "groq/openai/gpt-oss-120b"
_HOSTED_COMPRESSOR_MODEL = "cerebras/gpt-oss-120b"
_LOCAL_SCORING_MODEL = "gemma4:e4b"
_LOCAL_COMPRESSOR_MODEL = "phi3:mini"


def default_scoring_model() -> str:
    """Default model id for the scoring role (dec-122). Hosted wins; bare local name otherwise.

    Env overrides (AXON_SCORING_MODEL) and ollama/ prefixing are applied by the
    caller in runtime.py, so this returns the bare local id when the flag is off.
    """
    return _HOSTED_SCORING_MODEL if USE_HOSTED_LOCAL_ROLES else _LOCAL_SCORING_MODEL


def default_compressor_model() -> str:
    """Default model id for the compressor role (dec-122). Hosted wins; bare local name otherwise.

    Env overrides (AXON_CAVEMAN_MODEL / OLLAMA_MODEL_PRIMARY) and ollama/ prefixing
    are applied by the caller, so this returns the bare local id when the flag is off.
    """
    return _HOSTED_COMPRESSOR_MODEL if USE_HOSTED_LOCAL_ROLES else _LOCAL_COMPRESSOR_MODEL


_KNOWN_PROVIDERS = (
    "ollama/",
    "groq/",
    "cerebras/",
    "openrouter/",
    "anthropic/",
    "openai/",
    "nvidia_nim/",
)


def resolve_litellm_model(raw: str) -> str:
    """Normalize a configured model id to a full litellm id.

    Bare Ollama names (``phi3:mini``) keep working by getting an ``ollama/``
    prefix; anything already carrying a known provider prefix is left as-is.
    """
    return raw if raw.startswith(_KNOWN_PROVIDERS) else f"ollama/{raw}"


def litellm_kwargs(
    model: str,
    *,
    ollama_host: str,
    num_ctx: int | None = None,
) -> dict[str, object]:
    kwargs: dict[str, object] = {"model": model}
    if model.startswith("ollama/"):
        kwargs["api_base"] = ollama_host
        if num_ctx is not None:
            kwargs["extra_body"] = {"options": {"num_ctx": num_ctx}}
    return kwargs
