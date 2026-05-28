"""Draft pool for ADRs that failed at least one gate (dec-111).

Drafts live in ``.axon/adr-draft/{commit_hash}.md`` as plain Markdown
with YAML frontmatter. They are not indexed by the default retriever.
After ``dormancy_days`` (default 30), a draft is marked ``dormant`` in
its frontmatter — still recoverable via ``pb adr review --dormant``,
just out of the hot path.

The pool never expires destructively. Cleanup is opt-in via
``pb adr review --purge-dormant``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from axon.config.data_root import data_root

DEFAULT_DORMANCY_DAYS = 30
STALE_TTL_HOURS = 24


@dataclass
class DraftRecord:
    """Persisted draft state."""

    commit_hash: str
    title: str
    context: str
    decision: str
    rationale: str
    failed_layer: str
    failed_reason: str
    structural_mode: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_l1_full_at: datetime | None = None
    dormant: bool = False


def _draft_dir() -> Path:
    return data_root() / "adr-draft"


def write_draft(record: DraftRecord, *, draft_dir: Path | None = None) -> Path:
    """Persist a draft to ``adr-draft/{commit_hash}.md``."""
    target_dir = draft_dir or _draft_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{record.commit_hash}.md"

    frontmatter = _serialize_frontmatter(record)
    body = (
        f"# {record.title}\n\n"
        f"## Context\n\n{record.context}\n\n"
        f"## Decision\n\n{record.decision}\n\n"
        f"## Rationale\n\n{record.rationale}\n"
    )
    path.write_text(f"---\n{frontmatter}---\n\n{body}", encoding="utf-8")
    return path


def read_draft(path: Path) -> DraftRecord:
    """Parse a draft Markdown file back into a ``DraftRecord``."""
    raw = path.read_text(encoding="utf-8")
    if not raw.startswith("---\n"):
        raise ValueError(f"draft missing frontmatter: {path}")
    end = raw.find("\n---\n", 4)
    if end == -1:
        raise ValueError(f"draft frontmatter unterminated: {path}")
    fm_block = raw[4:end]
    fm = _parse_frontmatter(fm_block)
    body = raw[end + 5 :]

    title = fm.get("title", "")
    context = _extract_section(body, "Context")
    decision = _extract_section(body, "Decision")
    rationale = _extract_section(body, "Rationale")

    return DraftRecord(
        commit_hash=fm.get("commit_hash", ""),
        title=title,
        context=context,
        decision=decision,
        rationale=rationale,
        failed_layer=fm.get("failed_layer", ""),
        failed_reason=fm.get("failed_reason", ""),
        structural_mode=fm.get("structural_mode", "false") == "true",
        created_at=datetime.fromisoformat(fm.get("created_at", datetime.now(UTC).isoformat())),
        last_l1_full_at=(
            datetime.fromisoformat(fm["last_l1_full_at"])
            if fm.get("last_l1_full_at")
            else None
        ),
        dormant=fm.get("dormant", "false") == "true",
    )


def list_drafts(
    *, draft_dir: Path | None = None, include_dormant: bool = False
) -> list[DraftRecord]:
    """Return all drafts in the pool. Filters dormant unless requested."""
    target_dir = draft_dir or _draft_dir()
    if not target_dir.exists():
        return []
    records: list[DraftRecord] = []
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
    """Set ``dormant=true`` on a draft. Returns True if applied."""
    target_dir = draft_dir or _draft_dir()
    path = target_dir / f"{commit_hash}.md"
    if not path.exists():
        return False
    record = read_draft(path)
    record.dormant = True
    write_draft(record, draft_dir=target_dir)
    return True


def find_stale(
    *, draft_dir: Path | None = None, ttl_hours: int = STALE_TTL_HOURS
) -> list[DraftRecord]:
    """Drafts whose last L1-full check is older than ``ttl_hours`` (or never)."""
    threshold = datetime.now(UTC) - timedelta(hours=ttl_hours)
    stale: list[DraftRecord] = []
    for record in list_drafts(draft_dir=draft_dir, include_dormant=False):
        last = record.last_l1_full_at
        if last is None or last < threshold:
            stale.append(record)
    return stale


def auto_dormancy_sweep(
    *,
    draft_dir: Path | None = None,
    dormancy_days: int = DEFAULT_DORMANCY_DAYS,
) -> list[str]:
    """Mark all drafts older than ``dormancy_days`` as dormant.

    Returns the list of commit_hashes that were transitioned.
    """
    threshold = datetime.now(UTC) - timedelta(days=dormancy_days)
    transitioned: list[str] = []
    for record in list_drafts(draft_dir=draft_dir, include_dormant=False):
        if record.created_at < threshold:
            if mark_dormant(record.commit_hash, draft_dir=draft_dir):
                transitioned.append(record.commit_hash)
    return transitioned


# ── frontmatter helpers (tiny YAML subset; no PyYAML dep) ─────────────


def _serialize_frontmatter(record: DraftRecord) -> str:
    lines = [
        f"commit_hash: {record.commit_hash}",
        f"title: {_quote(record.title)}",
        f"failed_layer: {record.failed_layer}",
        f"failed_reason: {_quote(record.failed_reason)}",
        f"structural_mode: {'true' if record.structural_mode else 'false'}",
        f"created_at: {record.created_at.isoformat()}",
        f"dormant: {'true' if record.dormant else 'false'}",
    ]
    if record.last_l1_full_at is not None:
        lines.append(f"last_l1_full_at: {record.last_l1_full_at.isoformat()}")
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
    # Minimal scalar: wrap in double quotes; escape backslash and quote
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
