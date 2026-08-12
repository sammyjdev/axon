"""Tests for axon.adr.retraction — retraction detector (dec-110 part D).

The false-positive fixtures below are the *exact* strings from the 682-
message transcript run that measured this pattern (see the module
docstring / PR body): four hits where "estava errado" described an
artifact (a bug, a doc, an allowlist, a test), not a claim the author
withdrew. A test built only from strings invented for this file would
prove nothing about the actual failure mode - these are the real ones.
"""

from __future__ import annotations

from pathlib import Path

from axon.adr.lesson_pool import TELL_PROMPT, read_draft
from axon.adr.retraction import capture_retraction, detect_retraction


class TestFalsePositivesFromTranscript:
    """Real "estava errado" hits that are NOT retractions - must stay silent."""

    def test_o_que_estava_errado(self) -> None:
        assert detect_retraction("Achei o que estava errado desde o começo") is None

    def test_o_que_estava_errado_allowlist(self) -> None:
        assert detect_retraction("O que estava errado na allowlist") is None

    def test_e_que_estava_errado_teste(self) -> None:
        assert detect_retraction("o teste genérico é que estava errado") is None


class TestRealRetractionsDetected:
    def test_eu_estava_errado(self) -> None:
        sig = detect_retraction("Eu estava errado sobre isso, o bug é outro.")
        assert sig is not None
        assert sig.marker == "estava errado"

    def test_errei(self) -> None:
        sig = detect_retraction(
            "Errei ao assumir que o cache já tinha sido invalidado."
        )
        assert sig is not None
        assert sig.marker == "errei"

    def test_errei_nisso_excluded(self) -> None:
        # measured: "errei nisso/nesse/nessa" is noise, not a fresh retraction
        assert (
            detect_retraction("Errei nisso, mas o resto do raciocínio segue.")
            is None
        )

    def test_corrigindo_o_que(self) -> None:
        sig = detect_retraction("Corrigindo o que eu disse antes, o endpoint é POST.")
        assert sig is not None
        assert sig.marker == "corrigindo"

    def test_retiro_o_que(self) -> None:
        sig = detect_retraction("Retiro o que falei sobre o schema.")
        assert sig is not None
        assert sig.marker == "retiro/retrato"

    def test_falso_alarme(self) -> None:
        sig = detect_retraction("Falso alarme, o teste está passando.")
        assert sig is not None
        assert sig.marker == "falso alarme"

    def test_minha_hipotese_estava(self) -> None:
        sig = detect_retraction("Minha hipótese estava errada, o problema é outro.")
        assert sig is not None
        assert sig.marker == "diagnóstico mudou"

    def test_meu_diagnostico_mudou(self) -> None:
        sig = detect_retraction("Meu diagnóstico mudou depois de ver o log.")
        assert sig is not None
        assert sig.marker == "diagnóstico mudou"


class TestNoSignal:
    def test_empty_text(self) -> None:
        assert detect_retraction("") is None
        assert detect_retraction("   ") is None

    def test_unrelated_text(self) -> None:
        assert detect_retraction("Vou implementar o endpoint agora.") is None


class TestCaptureRetraction:
    def test_no_signal_writes_nothing(self, tmp_path: Path) -> None:
        result = capture_retraction("Vou implementar o endpoint agora.", draft_dir=tmp_path)
        assert result is None
        assert not any(tmp_path.iterdir()) if tmp_path.exists() else True

    def test_signal_enqueues_draft_with_tell_prompt(self, tmp_path: Path) -> None:
        path = capture_retraction(
            "Eu estava errado sobre isso, o bug é outro.", draft_dir=tmp_path
        )
        assert path is not None
        assert path.exists()
        record = read_draft(path)
        assert record.tell == TELL_PROMPT  # never auto-filled - human writes the "tell"
        assert "estava errado" in record.context

    def test_source_folded_into_context(self, tmp_path: Path) -> None:
        path = capture_retraction(
            "Retiro o que falei sobre o schema.",
            source="session-2026-08-12.md",
            draft_dir=tmp_path,
        )
        assert path is not None
        record = read_draft(path)
        assert "session-2026-08-12.md" in record.context

    def test_idempotent_does_not_overwrite(self, tmp_path: Path) -> None:
        text = "Falso alarme, o teste está passando."
        first = capture_retraction(text, draft_dir=tmp_path)
        assert first is not None
        record = read_draft(first)
        record.tell = "already reviewed by a human"
        from axon.adr.lesson_pool import write_draft

        write_draft(record, draft_dir=tmp_path)

        second = capture_retraction(text, draft_dir=tmp_path)
        assert second == first
        assert read_draft(second).tell == "already reviewed by a human"

    def test_writes_to_same_pool_lesson_draft_shape(self, tmp_path: Path) -> None:
        # dec-110: no second draft store - this must be a LessonDraftRecord
        # readable by the exact same lesson_pool.read_draft/list_drafts used
        # by the Lesson: trailer path.
        path = capture_retraction("Errei ao assumir isso.", draft_dir=tmp_path)
        assert path is not None
        from axon.adr.lesson_pool import list_drafts

        drafts = list_drafts(draft_dir=tmp_path)
        assert len(drafts) == 1
        assert drafts[0].commit_hash == path.stem
