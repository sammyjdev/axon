"""Session memory compressor.

Compresses session turns every 10 interactions using claude-haiku.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import litellm

from axon.router.llm_backend import (
    default_compressor_model,
    litellm_kwargs,
    resolve_litellm_model,
)

logger = logging.getLogger(__name__)

_COMPRESS_INTERVAL = 10
_MAX_SUMMARY_TOKENS = 400
# The context is bounded by size, not by a turn count: slicing a fixed tail
# silently dropped every turn before it, however short those turns were.
_MAX_CONTEXT_CHARS = 24_000
_MAX_TURN_CHARS = 800

_SYSTEM_PROMPT = (
    "You are a session memory compressor. "
    "Given a sequence of conversation turns, produce a concise summary (max 400 tokens) "
    "that preserves key decisions, open questions, and actionable items. "
    "Use bullet points. Be dense and precise. Do not repeat obvious context."
)


@dataclass
class SessionCompressor:
    turns: list[dict[str, str]] = field(default_factory=list)
    compressed_summary: str = ""
    _turn_count: int = field(default=0, init=False, repr=False)

    def add_turn(self, role: str, content: str) -> None:
        self.turns.append({"role": role, "content": content})
        self._turn_count += 1

    def should_compress(self) -> bool:
        return self._turn_count > 0 and self._turn_count % _COMPRESS_INTERVAL == 0

    def _context_lines(self) -> list[str]:
        """Newest-first walk, emitted oldest-first, stopping at the char budget.
        Long turns are clipped individually so one huge turn cannot evict the
        rest of the session."""
        lines: list[str] = []
        budget = _MAX_CONTEXT_CHARS
        for turn in reversed(self.turns):
            line = f"{turn['role'].upper()}: {turn['content'][:_MAX_TURN_CHARS]}"
            if len(line) > budget:
                break
            budget -= len(line)
            lines.append(line)
        lines.reverse()
        return lines

    @staticmethod
    def _backend() -> dict[str, object]:
        """Route through the compressor role the router already resolves
        (dec-122). The old hardcoded Anthropic id made session capture depend
        on an API key that a subscription machine has no reason to hold."""
        import os

        model = resolve_litellm_model(
            os.environ.get("AXON_SESSION_COMPRESSOR_MODEL")
            or os.environ.get("AXON_CAVEMAN_MODEL")
            or default_compressor_model()
        )
        return litellm_kwargs(
            model,
            ollama_host=os.environ.get("AXON_OLLAMA_LOCAL_HOST", "http://127.0.0.1:11434"),
            num_ctx=int(os.environ.get("AXON_CAVEMAN_NUM_CTX", "4096")),
        )

    async def compress(self) -> str:
        """Compresses current turns into a summary, replacing stored turns."""
        if not self.turns:
            return self.compressed_summary

        context = "\n".join(self._context_lines())
        if self.compressed_summary:
            context = f"PREVIOUS SUMMARY:\n{self.compressed_summary}\n\nNEW TURNS:\n{context}"

        response = await litellm.acompletion(
            **self._backend(),
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": context},
            ],
            max_tokens=_MAX_SUMMARY_TOKENS,
        )
        summary = response.choices[0].message.content or ""
        self.compressed_summary = summary
        # Keep only the last 2 turns for continuity
        self.turns = self.turns[-2:]
        logger.info("Session compressed to %d chars", len(summary))
        return summary

    async def maybe_compress(self) -> None:
        """Compresses if the interval has been reached."""
        if self.should_compress():
            await self.compress()
