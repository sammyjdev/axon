# TIL Automation

Status: active workflow guidance

This document isolates the TIL and HOW-TO automation story from the broader
setup docs. The goal is to make daily knowledge capture cheap, promote the
right notes into durable references, and keep the automation explicit enough to
review.

## Current Capabilities

AXON already supports four useful TIL automation steps:

- capture a TIL into `knowledge/daily/<date>/`
- list pending TILs that were not promoted yet
- promote today's TILs into HOW-TO notes
- suggest deeper topics from recent daily notes

## Commands

### Capture a TIL

```bash
pb til --tags qdrant,ids "Qdrant rejects SHA1 hex ids; use uuid5 instead"
```

What it does:

- creates a Markdown note under `knowledge/daily/<today>/`
- adds front-matter with tags, date, `type: til`, and `promoted: false`

### List pending TILs

```bash
pb til --list
```

What it does:

- scans `knowledge/` for `til-*.md`
- returns notes that still contain `promoted: false`

### Promote today's TILs

```bash
pb til --promote-today
```

What it does today:

- scans today's TIL files
- asks a lightweight local model whether each TIL should stay as-is or become
  a HOW-TO
- creates `howto-*.md` next to the original TIL when promotion wins
- marks the original TIL as `promoted: true`

Current promotion criteria in code:

- concrete code snippet
- identifiable pitfall or common error
- real usage context
- reproducible procedural value

### Convert one TIL manually

```bash
pb til howto --from knowledge/daily/2026-05-08/til-example.md
```

Use this when:

- the automatic promotion rule said `KEEP`
- you still know the note should become a durable HOW-TO
- you want to bypass the daily batch

### Suggest deep topics

```bash
pb deep suggest
```

What it does:

- reviews recent daily notes
- compares them with existing `knowledge/deep/`
- suggests up to 3 deeper study topics when repeated shallow fixes point to a
  missing conceptual note

## Recommended Operating Pattern

### Daily capture

- save short factual TILs during work
- keep each TIL focused on one lesson or pitfall
- include real code or command fragments when possible

### End-of-day review

- run `pb til --list`
- run `pb til --promote-today`
- spot-check generated HOW-TO notes before treating them as verified long-term
  references

### Weekly consolidation

- run `pb deep suggest`
- convert recurring topics into deeper notes under `knowledge/deep/`

## Limits of the Current Automation

- promotion is model-assisted, not deterministic
- generated HOW-TO notes should still be reviewed by a human
- promotion currently writes HOW-TO files next to the source TIL instead of a
  dedicated `knowledge/howto/` area
- there is no persistent audit trail yet for why a TIL was promoted or kept
- there is no outcome/failure memory yet for measuring promotion quality

## Roadmap Direction

TIL automation should eventually connect with the broader engine roadmap:

- `OutcomeStore`: remember which HOW-TOs actually helped later
- `FailureStore`: remember repeated pitfalls before they become deep notes
- `TraceStore`: record why a promotion happened
- staleness detection: down-rank stale HOW-TO notes
- profile-driven automation: lighter workflows for small personal vaults,
  stronger review gates for team/shared vaults
