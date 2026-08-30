"""
Barreira de acesso às collections de vetor por contexto.
work só é acessível com ctx='work' explícito — protege IP da Avangrid.
"""

from axon.context.registry import (
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
    Nunca expõe 'work' sem ctx='work' explícito; qualquer outro contexto busca
    em todas as não-protegidas (dec-131).
    """
    normalized_ctx = normalize_context(ctx)
    if normalized_ctx in PROTECTED_CONTEXTS:
        return [normalized_ctx]
    # dec-131: a non-protected ctx orders retrieval, it does not partition it.
    # It used to return [ctx], which restricted exactly as hard as 'work' and
    # made a question asked under one context unable to reach material indexed
    # under another (code lands in 'personal', decisions in 'knowledge').
    # The ranking preference lives in the store; see _rank_and_limit(prefer_ctx=).
    return list(DEFAULT_SEARCH_CONTEXTS)


def all_collections() -> list[str]:
    return list(_REGISTRY.keys())
