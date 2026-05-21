# dec-105 — Migrate domain models to Pydantic v2

- Status: accepted
- Date: 2026-05-21
- Refines: CLAUDE.md convention "Prefer dataclass over ad-hoc dicts"

## Context

CLAUDE.md instructs preferring `@dataclass` for models. AXON introduces models
that need validation, regex constraints, score ranges, and round-trip
serialization to markdown frontmatter (`Decision`, `Symbol`, `Edge`). An audit
(`docs/MIGRATION_PYDANTIC.md`) found **71** `@dataclass` declarations — far more
than the ~9 the original AXON draft assumed. Of these, only a subset are
persisted/serialized data models; the rest are internal value objects, config
objects, or service classes.

## Decision

- New core models (`Decision`, `Symbol`, `Edge`) are Pydantic v2.
- Existing **data models** that are persisted or cross a serialization boundary
  migrate to Pydantic v2 (bucket A of MIGRATION_PYDANTIC.md, ~28 models).
- Internal value/config objects and service classes that happen to use
  `@dataclass` stay as dataclasses (buckets B and C). Migrating a service class
  (e.g. `EmbedderEngine`, `VaultWatcher`) to `BaseModel` is an anti-pattern and
  is explicitly out of scope.
- The CLAUDE.md convention is updated to: "domain/data models use Pydantic v2;
  internal config and value objects may use dataclass."

## Rationale

- Pydantic gives validation and round-trip safety where data leaves the process.
- Forcing every dataclass to Pydantic adds cost and risk with no benefit for
  in-process value objects and service classes.

## Consequences

- The `Chunk` model is in bucket A; its migration touches the D5 chunker suite.
  Gate D5 rule: the 118 chunker assertions are preserved; only mechanical
  constructor adaptation is allowed.
- This refines an earlier broad "migrate everything" intent — the audit revealed
  service classes mixed into the `@dataclass` count, which must not migrate.
  See MIGRATION_PYDANTIC.md for the per-model classification.
