"""Single source of token estimation for chunker + pipeline."""

_TOKENS_PER_CHAR = 0.35


def estimate_tokens(text: str) -> int:
    """Estimate token count as 0.35 * len(text). Returns at least 1."""
    return max(1, int(len(text) * _TOKENS_PER_CHAR))
