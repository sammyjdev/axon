from __future__ import annotations

from axon.context.detector import ContextDetector

# Vocabulário técnico genérico que pontua alto em CONTENT_SIGNALS["knowledge"].
# Um projeto de trabalho conversa exatamente nesses termos.
KNOWLEDGE_BIASED = "como o docker do rag indexa embedding vector"

AFYA_PATHS = [
    "/Users/samdev/dev/afya",
    "/Users/samdev/dev/afya/poc-medicamentos-ia",
    "/Users/samdev/dev/afya/rodar local/iago-server",
]


def test_afya_root_resolves_to_work() -> None:
    detector = ContextDetector(None)

    for cwd in AFYA_PATHS:
        assert detector.detect("onde fica o loader", cwd=cwd).context == "work"


def test_work_path_beats_biased_content() -> None:
    """O path é prova, não pista: dentro de uma raiz work o conteúdo não vota.

    Sem o short-circuit, o cwd (0.4) empata com knowledge (0.4) e o desempate do
    max() — ordem do dict — manda material de trabalho para knowledge, onde busca
    sem ctx o alcança.
    """
    detector = ContextDetector(None)

    for cwd in AFYA_PATHS:
        assert detector.detect(KNOWLEDGE_BIASED, cwd=cwd).context == "work"


def test_paths_outside_work_are_unaffected() -> None:
    detector = ContextDetector(None)

    assert detector.detect(KNOWLEDGE_BIASED, cwd="/Users/samdev/dev/axon").context == "knowledge"
    assert detector.detect(KNOWLEDGE_BIASED, cwd=None).context == "knowledge"
    assert detector.detect("onde fica o loader", cwd="/Users/samdev/dev/axon").context == "general"
