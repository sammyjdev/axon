"""Every ADR on disk must appear in the index that claims to list them all.

`docs/ADR.md` is the entry point for architectural decisions, so an ADR missing
from it is an ADR nobody finds. The #92 provenance work shipped with no ADR at
all and went unnoticed until the round was reviewed as a whole.
"""

from __future__ import annotations

import re
from pathlib import Path

DOCS = Path(__file__).resolve().parents[1] / "docs"
INDEX = DOCS / "ADR.md"
DECISIONS = DOCS / "decisions"


def test_every_decision_file_is_listed_in_the_index() -> None:
    index = INDEX.read_text()
    on_disk = sorted(p.name for p in DECISIONS.glob("dec-*.md"))

    assert on_disk, "no decision files found — the glob is wrong"

    missing = [name for name in on_disk if name not in index]

    assert not missing, (
        f"{INDEX.name} does not link: {missing}. It is the entry point for "
        "decisions, so an unlisted ADR is one nobody will find."
    )


def test_the_index_does_not_link_files_that_do_not_exist() -> None:
    linked = set(re.findall(r"decisions/(dec-[a-z0-9-]+\.md)", INDEX.read_text()))
    on_disk = {p.name for p in DECISIONS.glob("dec-*.md")}

    dangling = sorted(linked - on_disk)

    assert not dangling, f"{INDEX.name} links missing files: {dangling}"
