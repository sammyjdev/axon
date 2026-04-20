from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import litellm

_SUGGESTION_PROMPT = """
Você é um mentor técnico analisando as notas diárias de um
Senior Java Engineer.

Notas daily da semana:
{daily_notes}

Notas deep existentes:
{deep_index}

Identifique até 3 gaps de conhecimento: situações onde o
engenheiro resolveu um problema sem entender o fundamento
por trás, ou onde múltiplos TILs apontam para o mesmo
conceito não estudado em profundidade.

Para cada gap, responda em JSON:
{{
  "topic": "nome do tópico",
  "why": "por que é um gap (cite as notas daily)",
  "suggested_title": "título para nota deep",
  "starting_questions": ["pergunta 1", "pergunta 2"]
}}

Responda apenas com array JSON, sem texto extra.
"""


def _created_after(path: Path, cutoff: date) -> bool:
    try:
        content = path.read_text()
        # Tenta extrair `created:` do front-matter
        for line in content.splitlines():
            if line.startswith("created:"):
                created_str = line.split(":", 1)[1].strip()
                return date.fromisoformat(created_str[:10]) >= cutoff
    except Exception:
        pass
    # Fallback: mtime do arquivo
    import datetime
    mtime = datetime.datetime.fromtimestamp(path.stat().st_mtime).date()
    return mtime >= cutoff


async def suggest_deep_topics() -> list[dict]:
    vault = Path.home() / "vault" / "knowledge"
    week_ago = date.today() - timedelta(days=7)

    daily_notes = [
        f.read_text() for f in vault.rglob("daily/**/*.md")
        if _created_after(f, week_ago)
    ]
    deep_index = [f.stem for f in vault.rglob("deep/**/*.md")]

    if not daily_notes:
        return []

    response = await litellm.acompletion(
        model="ollama/gemma4:26b",
        messages=[{
            "role": "user",
            "content": _SUGGESTION_PROMPT.format(
                daily_notes="\n---\n".join(daily_notes[:20]),
                deep_index="\n".join(deep_index) if deep_index else "(nenhuma ainda)",
            ),
        }],
        max_tokens=1000,
    )

    raw = response.choices[0].message.content
    # Extrai apenas o bloco JSON caso o modelo retorne texto extra
    start = raw.find("[")
    end = raw.rfind("]") + 1
    if start == -1 or end == 0:
        return []
    return json.loads(raw[start:end])
