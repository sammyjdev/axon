"""Draft pool for lessons captured via the ``Lesson:`` commit trailer.

Sibling to ``draft_pool.py`` (ADR drafts), not a variant record folded
into it: a lesson draft has no LLM-inferred payload and no gate outcome
to carry — it's written straight from ``on_commit`` the moment the
trailer is seen. An ADR draft only exists because the LLM's proposal
*failed* a gate; a lesson draft is the trailer itself, immediately.
Reusing ``DraftRecord`` would mean every reader reasons about
``failed_layer``/``structural_mode``/``last_l1_full_at`` fields that are
always empty for lessons, and vice versa.

The one thing genuinely shared with the ADR pool — the 30-day dormancy
rule — lives in ``draft_dormancy.py`` and is reused here, not
reimplemented.

A draft is a file with the commit hash, the trailer title, the files
touched (as machine-supplied context, no LLM involved), and a ``tell``
field left as an explicit prompt: what did the author SEE while still
confused, not the diagnosis after the fact. That's what makes a lesson
retrievable later — a blank template produces a badly-written lesson,
which is worse than no draft at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from axon.adr.draft_dormancy import DEFAULT_DORMANCY_DAYS, due_for_dormancy
from axon.config.data_root import data_root

TELL_PROMPT = (
    "TODO: what did you SEE while still confused - not the diagnosis afterward"
)


@dataclass
class LessonDraftRecord:
    """Persisted lesson-draft state."""

    commit_hash: str
    title: str
    context: str
    tell: str = TELL_PROMPT
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    dormant: bool = False


def _draft_dir() -> Path:
    return data_root() / "lesson-draft"


def write_draft(record: LessonDraftRecord, *, draft_dir: Path | None = None) -> Path:
    """Persist a draft to ``lesson-draft/{commit_hash}.md``."""
    target_dir = draft_dir or _draft_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{record.commit_hash}.md"

    frontmatter = _serialize_frontmatter(record)
    body = (
        f"# {record.title}\n\n"
        f"## Tell\n\n{record.tell}\n\n"
        f"## Context\n\n{record.context}\n"
    )
    path.write_text(f"---\n{frontmatter}---\n\n{body}", encoding="utf-8")
    return path


def read_draft(path: Path) -> LessonDraftRecord:
    """Parse a draft Markdown file back into a ``LessonDraftRecord``."""
    raw = path.read_text(encoding="utf-8")
    if not raw.startswith("---\n"):
        raise ValueError(f"draft missing frontmatter: {path}")
    end = raw.find("\n---\n", 4)
    if end == -1:
        raise ValueError(f"draft frontmatter unterminated: {path}")
    fm_block = raw[4:end]
    fm = _parse_frontmatter(fm_block)
    body = raw[end + 5 :]

    return LessonDraftRecord(
        commit_hash=fm.get("commit_hash", ""),
        title=fm.get("title", ""),
        context=_extract_section(body, "Context"),
        tell=_extract_section(body, "Tell") or TELL_PROMPT,
        created_at=datetime.fromisoformat(
            fm.get("created_at", datetime.now(UTC).isoformat())
        ),
        dormant=fm.get("dormant", "false") == "true",
    )


def list_drafts(
    *, draft_dir: Path | None = None, include_dormant: bool = False
) -> list[LessonDraftRecord]:
    """Return all lesson drafts in the pool. Filters dormant unless requested."""
    target_dir = draft_dir or _draft_dir()
    if not target_dir.exists():
        return []
    records: list[LessonDraftRecord] = []
    for path in sorted(target_dir.glob("*.md")):
        try:
            record = read_draft(path)
        except (ValueError, OSError):
            continue
        if record.dormant and not include_dormant:
            continue
        records.append(record)
    return records


def mark_dormant(commit_hash: str, *, draft_dir: Path | None = None) -> bool:
    """Set ``dormant=true`` on a lesson draft. Returns True if applied."""
    target_dir = draft_dir or _draft_dir()
    path = target_dir / f"{commit_hash}.md"
    if not path.exists():
        return False
    record = read_draft(path)
    record.dormant = True
    write_draft(record, draft_dir=target_dir)
    return True


def auto_dormancy_sweep(
    *,
    draft_dir: Path | None = None,
    dormancy_days: int = DEFAULT_DORMANCY_DAYS,
) -> list[str]:
    """Mark all lesson drafts older than ``dormancy_days`` as dormant.

    Returns the list of commit_hashes that were transitioned.
    """
    records = list_drafts(draft_dir=draft_dir, include_dormant=False)
    due = due_for_dormancy(records, dormancy_days=dormancy_days)
    transitioned: list[str] = []
    for record in due:
        if mark_dormant(record.commit_hash, draft_dir=draft_dir):
            transitioned.append(record.commit_hash)
    return transitioned


# ── frontmatter helpers (tiny YAML subset; no PyYAML dep). Mirrors
# draft_pool.py's helpers but kept separate: the field sets diverge
# (no failed_layer/structural_mode/last_l1_full_at here), so a shared
# serializer would need dict-of-fields dispatch for two call sites —
# not worth it for four small functions. ─────────────────────────────


def _serialize_frontmatter(record: LessonDraftRecord) -> str:
    lines = [
        f"commit_hash: {record.commit_hash}",
        f"title: {_quote(record.title)}",
        f"created_at: {record.created_at.isoformat()}",
        f"dormant: {'true' if record.dormant else 'false'}",
    ]
    return "\n".join(lines) + "\n"


def _parse_frontmatter(block: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in block.splitlines():
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        out[k.strip()] = _unquote(v.strip())
    return out


def _quote(s: str) -> str:
    escaped = s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escaped}"'


def _unquote(s: str) -> str:
    if s.startswith('"') and s.endswith('"') and len(s) >= 2:
        inner = s[1:-1]
        return inner.replace("\\n", "\n").replace('\\"', '"').replace("\\\\", "\\")
    return s


def _extract_section(body: str, name: str) -> str:
    """Pull the contents of ``## {name}`` until the next ``##`` or EOF."""
    marker = f"## {name}\n"
    idx = body.find(marker)
    if idx == -1:
        return ""
    start = idx + len(marker)
    end = body.find("\n## ", start)
    if end == -1:
        end = len(body)
    return body[start:end].strip()
