from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import litellm

from prometheus.config.runtime import load_runtime_config

_RUNTIME = load_runtime_config()
KNOWLEDGE_PATH = _RUNTIME.vault_context_root("knowledge")
TODAY = date.today().isoformat()

_PROMOTION_CRITERIA = """
Analise este TIL e decida se deve ser promovido para HOW-TO.

Promova se tiver DOIS ou mais:
- Trecho de código concreto
- Uma armadilha ou erro comum identificado
- Contexto de uso real (onde foi aplicado)
- Algo reproduzível (não só uma observação)

Responda apenas: PROMOTE ou KEEP
"""

_HOWTO_TEMPLATE = """
Converta este TIL em um HOW-TO seguindo exatamente este formato:

---
tags: [extraia as tags do TIL]
created: {today}
type: howto
verified: true
promoted_from: {til_name}
---

# HOW-TO: [título acionável]

## Quando usar
[contexto de uso]

## Código mínimo
[código do TIL formatado]

## Armadilha principal
[problema identificado]

## Usado em
[contexto do TIL]

TIL original:
{til_content}
"""


def find_todays_tils() -> list[Path]:
    return [
        f for f in KNOWLEDGE_PATH.rglob("til-*.md")
        if TODAY in f.name and not _is_promoted(f)
    ]


def _is_promoted(path: Path) -> bool:
    return "promoted: true" in path.read_text()


def should_promote(til_content: str) -> bool:
    response = litellm.completion(
        model="ollama/gemma4:e4b",
        messages=[
            {"role": "system", "content": _PROMOTION_CRITERIA},
            {"role": "user", "content": til_content},
        ],
        max_tokens=10,
    )
    return "PROMOTE" in response.choices[0].message.content


def promote_to_howto(til_path: Path) -> Path:
    til_content = til_path.read_text()
    prompt = _HOWTO_TEMPLATE.format(
        today=TODAY,
        til_name=til_path.name,
        til_content=til_content,
    )
    response = litellm.completion(
        model="ollama/gemma4:e4b",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=800,
    )
    howto_content = response.choices[0].message.content
    howto_path = til_path.parent / til_path.name.replace("til-", "howto-")
    howto_path.write_text(howto_content)
    # Marca TIL como promovido
    updated = re.sub(r"promoted: false", "promoted: true", til_content)
    til_path.write_text(updated)
    return howto_path


def run() -> None:
    tils = find_todays_tils()
    if not tils:
        return

    print(f"Analisando {len(tils)} TIL(s) do dia...")
    promoted = 0

    for til_path in tils:
        content = til_path.read_text()
        if should_promote(content):
            howto_path = promote_to_howto(til_path)
            print(f"  Promovido: {til_path.name} → {howto_path.name}")
            promoted += 1
        else:
            print(f"  Mantido como TIL: {til_path.name}")

    if promoted:
        print(f"\n{promoted} HOW-TO(s) criados. Revise quando quiser.")


if __name__ == "__main__":
    run()
