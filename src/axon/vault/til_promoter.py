from __future__ import annotations

import json
import re
import urllib.request
from datetime import date
from pathlib import Path

from axon.config.runtime import load_runtime_config

_RUNTIME = load_runtime_config()
KNOWLEDGE_PATH = _RUNTIME.vault_context_root("knowledge")
TODAY = date.today().isoformat()

# Straight to the local Ollama server: litellm was only a wrapper around this
# call, and its filelock<4.0 pin kept 13 filelock CVEs open.
_OLLAMA_CHAT_URL = "http://localhost:11434/api/chat"
_MODEL = "gemma4:e4b"
_TIMEOUT_S = 120

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
    return [f for f in KNOWLEDGE_PATH.rglob("til-*.md") if TODAY in f.name and not _is_promoted(f)]


def _is_promoted(path: Path) -> bool:
    return "promoted: true" in path.read_text()


def _chat(messages: list[dict[str, str]], *, max_tokens: int) -> str:
    payload = {
        "model": _MODEL,
        "messages": messages,
        "stream": False,
        "options": {"num_predict": max_tokens},
    }
    request = urllib.request.Request(  # noqa: S310 - fixed local Ollama endpoint
        _OLLAMA_CHAT_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=_TIMEOUT_S) as response:  # noqa: S310
        return json.loads(response.read())["message"]["content"]


def should_promote(til_content: str) -> bool:
    content = _chat(
        [
            {"role": "system", "content": _PROMOTION_CRITERIA},
            {"role": "user", "content": til_content},
        ],
        max_tokens=10,
    )
    return "PROMOTE" in content


def promote_to_howto(til_path: Path) -> Path:
    til_content = til_path.read_text()
    prompt = _HOWTO_TEMPLATE.format(
        today=TODAY,
        til_name=til_path.name,
        til_content=til_content,
    )
    howto_content = _chat([{"role": "user", "content": prompt}], max_tokens=800)
    howto_path = til_path.parent / til_path.name.replace("til-", "howto-")
    howto_path.write_text(howto_content)
    # Marca TIL como promovido
    updated = re.sub(r"promoted: false", "promoted: true", til_content)
    til_path.write_text(updated)
    return howto_path


def run() -> list[Path]:
    """Promote eligible TILs of the day into HOW-TOs.

    Returns the list of HOW-TO paths that were just created, in the order they
    were produced. An empty list means nothing was promoted (no TILs pending,
    or the LLM judged none worth promoting). The caller is expected to reindex
    the returned paths if it wants them searchable immediately.
    """
    tils = find_todays_tils()
    if not tils:
        return []

    print(f"Analisando {len(tils)} TIL(s) do dia...")
    promoted_paths: list[Path] = []

    for til_path in tils:
        content = til_path.read_text()
        if should_promote(content):
            howto_path = promote_to_howto(til_path)
            print(f"  Promovido: {til_path.name} → {howto_path.name}")
            promoted_paths.append(howto_path)
        else:
            print(f"  Mantido como TIL: {til_path.name}")

    if promoted_paths:
        print(f"\n{len(promoted_paths)} HOW-TO(s) criados. Revise quando quiser.")
    return promoted_paths


if __name__ == "__main__":
    run()
