from __future__ import annotations

from enum import Enum

import ollama


class TaskType(str, Enum):
    TRIVIAL_COMPLETION = "TRIVIAL_COMPLETION"
    CODE_ANALYSIS = "CODE_ANALYSIS"
    ARCHITECTURE = "ARCHITECTURE"
    DEEP_REASONING = "DEEP_REASONING"
    LOCAL_ONLY = "LOCAL_ONLY"
    UNKNOWN = "UNKNOWN"


_CLASSIFIER_PROMPT = """
Classifique a task em UMA das categorias abaixo. Responda apenas com o nome da categoria.

Categorias:
- TRIVIAL_COMPLETION: autocompletar, snippets curtos, perguntas factuais simples
- CODE_ANALYSIS: revisão de código, debug, refactor, análise de arquitetura existente
- ARCHITECTURE: design de sistema, decisões de arquitetura novas, planejamento de fase
- DEEP_REASONING: raciocínio complexo, trade-offs, múltiplas perspectivas técnicas
- LOCAL_ONLY: apenas informação local, sem necessidade de LLM cloud

Responda apenas com uma das categorias acima, sem texto extra.
"""


def classify_task(content: str) -> TaskType:
    try:
        response = ollama.chat(
            model="phi3:mini",
            messages=[
                {"role": "system", "content": _CLASSIFIER_PROMPT},
                {"role": "user", "content": content},
            ],
        )
        raw = response["message"]["content"].strip().upper()
        # Normaliza variações comuns
        for member in TaskType:
            if member.value in raw:
                return member
        return TaskType.UNKNOWN
    except Exception:
        # Ollama não disponível: fallback conservador
        return TaskType.CODE_ANALYSIS
