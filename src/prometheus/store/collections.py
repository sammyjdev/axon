"""
Barreira de acesso às collections Qdrant por contexto.
work só é acessível com ctx='work' explícito — protege IP da Avangrid.
"""

_REGISTRY: dict[str, dict] = {
    "personal":  {"restricted": False},
    "career":    {"restricted": False},
    "knowledge": {"restricted": False},
    "work":      {"restricted": True},
}


def get_search_collections(ctx: str | None) -> list[str]:
    """
    Retorna as collections disponíveis para busca dado o contexto ativo.
    Nunca expõe 'work' sem ctx='work' explícito.
    """
    if ctx == "work":
        return ["work"]
    return [name for name, meta in _REGISTRY.items() if not meta["restricted"]]


def all_collections() -> list[str]:
    return list(_REGISTRY.keys())
