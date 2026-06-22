"""Guard: file_walk._EXCLUDED_DIR_NAMES is a local copy of
pipeline.EXCLUDED_DIR_NAMES (duplicated to avoid a circular import). They must
never drift - the git-walk rglob fallback would otherwise apply different
exclusions than the main pipeline path."""

from __future__ import annotations


def test_excluded_dir_names_stay_in_sync() -> None:
    from axon.embedder.pipeline import EXCLUDED_DIR_NAMES
    from axon.repo.file_walk import _EXCLUDED_DIR_NAMES

    assert _EXCLUDED_DIR_NAMES == EXCLUDED_DIR_NAMES, (
        "file_walk._EXCLUDED_DIR_NAMES drifted from pipeline.EXCLUDED_DIR_NAMES. "
        f"only in pipeline: {sorted(EXCLUDED_DIR_NAMES - _EXCLUDED_DIR_NAMES)}; "
        f"only in file_walk: {sorted(_EXCLUDED_DIR_NAMES - EXCLUDED_DIR_NAMES)}. "
        "Keep the two sets identical (they are intentionally duplicated to avoid "
        "a circular import)."
    )
