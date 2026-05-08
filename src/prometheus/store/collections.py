"""
Barreira de acesso às collections Qdrant por contexto.
work só é acessível com ctx='work' explícito — protege IP da Avangrid.
"""

from prometheus.context.registry import (
    DEFAULT_SEARCH_CONTEXTS,
    PROTECTED_CONTEXTS,
    normalize_context,
)

_REGISTRY: dict[str, dict] = {
    "personal": {"restricted": False},
    "career": {"restricted": False},
    "knowledge": {"restricted": False},
    "saas": {"restricted": False},
    "work": {"restricted": True},
}


def get_search_collections(ctx: str | None) -> list[str]:
    """
    Retorna as collections disponíveis para busca dado o contexto ativo.
    Nunca expõe 'work' sem ctx='work' explícito.
    """
    normalized_ctx = normalize_context(ctx)
    if normalized_ctx in PROTECTED_CONTEXTS:
        return [normalized_ctx]
    return list(DEFAULT_SEARCH_CONTEXTS)


def all_collections() -> list[str]:
    return list(_REGISTRY.keys())
