"""Session memory compressor.

Compresses session turns every 10 interactions using claude-haiku.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import litellm

logger = logging.getLogger(__name__)

_COMPRESS_INTERVAL = 10
_COMPRESS_MODEL = "claude-haiku-4-5-20251001"
_MAX_SUMMARY_TOKENS = 400

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

    async def compress(self) -> str:
        """Compresses current turns into a summary, replacing stored turns."""
        if not self.turns:
            return self.compressed_summary

        context = "\n".join(
            f"{t['role'].upper()}: {t['content'][:200]}" for t in self.turns[-_COMPRESS_INTERVAL:]
        )
        if self.compressed_summary:
            context = f"PREVIOUS SUMMARY:\n{self.compressed_summary}\n\nNEW TURNS:\n{context}"

        response = await litellm.acompletion(
            model=_COMPRESS_MODEL,
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
