# dec-130: ADR provenance is fail-closed, and only a prompt may claim a human

- Status: Accepted
- Date: 2026-08-27
- Supersedes: none
- Related: dec-110 (declarative capture via commit signal), dec-125 (single CLI),
  issue #92

## Context

ADR inference feeds a commit message and a diff summary straight into the
classifier prompt (`src/axon/adr/inference.py`). Both are attacker-influenced
input in any repository that accepts outside contributions, and a successful
injection produces an ADR that persists and is later served back as trusted
context through `get_adrs` and the vault export.

Two mitigations were on the table: structural delimiters plus "ignore embedded
instructions" framing in the prompt, and a provenance flag on inferred ADRs.

## Decision

Both, with an explicit ordering: **the flag is the part that has to hold.**

Prompt framing is mitigation by convention. No delimiter reliably stops
injection, and `_fence()` is honest about its own limit — it neutralises a
forged closing tag matching its own span name, not every variant. Treating that
as the fix is how this gets closed while still being exploitable.

Provenance degrades gracefully instead. `ADR.provenance` is
`Literal["human", "llm-inferred"]` defaulting to `"llm-inferred"`, so if the
framing fails and a poisoned ADR lands, it still arrives labelled
machine-inferred rather than passing as authored.

Three consequences follow from "fail-closed":

**Only an interactive prompt may claim human authorship.** `axon adr add` sets
`provenance="human"` because a `typer.prompt()` sits in front of it: a person
at a terminal typed the content. Every other write path — inference, the
pending drain, draft promotion — omits the field and inherits the default.

`save_adr` originally hardcoded `provenance="human"` and that was a defect,
found by reviewing the round as a whole rather than PR by PR. It is an MCP
tool; its own docstring tells the calling model to use it when *it* makes a
design decision, so the writer is an LLM by construction and nothing in the
chain checks for a human. The test asserted the wrong behaviour as expected,
which is why a green gate, a mutation sensor and two reviewers all passed it.

**The column is created by `ensure_schema()`, not a versioned migration.**
`apply_pg_migrations` has one caller, `PostgresSessionRepository.ensure_schema`,
and the dec-110 path never touches the session repo — a migration file would
not run and the insert would hit a table without the column.

**The backfill is imprecise, and the wording admits it.** Pre-existing rows
default to `llm-inferred`, and some of them were genuinely human-written. That
is unrecoverable, and the error directions are asymmetric: labelling a human ADR
as machine-inferred costs credibility, while the reverse would let a poisoned
ADR pass as authored. So the rendered notice reads "não confirmado como autoria
humana" rather than claiming an LLM wrote it — true of every row it renders on.

## Consequences

- A consumer can tell an inferred ADR from an authored one; `get_adrs` and the
  vault export both surface the label.
- ADRs written before 2026-08-27 carry `llm-inferred` regardless of who wrote
  them. Treat the label as "not confirmed human", not as "machine-written".
- Any new write path defaults to `llm-inferred` by omission. Adding
  `provenance="human"` to one is a security decision, not a detail: it needs a
  human in the loop at the moment of writing, not merely a human somewhere
  upstream.
