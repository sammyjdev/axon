"""A file too large to be source is not worth indexing.

`worker-configuration.d.ts` in revvo is 500 KB of wrangler-generated types.
Nobody searches for it, it has no semantic value, and sending it cost the
project its entire index (422 from the provider, 2026-08-29).

The rule is size, not name. A list of generated-file patterns (*.d.ts,
*-lock.json, *.min.js) ages badly and never covers the next tool's output;
a byte ceiling catches all of them and needs no maintenance. It is the same
name-independent reasoning EXCLUDED_DIR_NAMES already uses for virtualenvs.

Measured across revvo, axon and lina: exactly one file exceeds 200 KB, and it
is the generated one. The ceiling costs nothing in real source.
"""

from __future__ import annotations

from pathlib import Path

from axon.embedder.pipeline import MAX_INDEXABLE_FILE_BYTES, iter_supported_files


def test_a_file_over_the_ceiling_is_skipped(tmp_path: Path) -> None:
    (tmp_path / "generated.ts").write_text("x" * (MAX_INDEXABLE_FILE_BYTES + 1))

    found = [str(f[0] if isinstance(f, tuple) else f) for f in iter_supported_files(tmp_path)]

    assert not any("generated.ts" in f for f in found), (
        "an oversized generated file reached the indexer; one of these took a "
        "whole project's index down with a 422"
    )


def test_normal_source_is_still_indexed(tmp_path: Path) -> None:
    (tmp_path / "real.py").write_text("def f():\n    return 1\n")

    found = [str(f[0] if isinstance(f, tuple) else f) for f in iter_supported_files(tmp_path)]

    assert any("real.py" in f for f in found)


def test_the_ceiling_leaves_room_for_genuinely_large_source(tmp_path: Path) -> None:
    """A big hand-written module must not be caught by this."""
    (tmp_path / "big_but_real.py").write_text("# a line of source\n" * 5_000)

    found = [str(f[0] if isinstance(f, tuple) else f) for f in iter_supported_files(tmp_path)]

    assert any("big_but_real.py" in f for f in found)
