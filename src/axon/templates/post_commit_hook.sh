#!/usr/bin/env bash
# AXON ADR inference hook (legacy template).
# Installed by: `pb adr hook` (deprecated) or `pb hooks install --apply` (dec-113).
#
# Per dec-110, ADR inference only fires when the commit message starts
# with `arch:` / `decision:` or carries an `ADR-Decision:` trailer.
# The CLI handles signal detection; this hook just delegates.

PROJECT=$(basename "$(git rev-parse --show-toplevel)")

# Run ADR inference (non-blocking: errors don't fail the commit).
# The signal gate inside `pb adr infer-commit` short-circuits when the
# commit lacks an architectural signal.
pb adr infer-commit --project "$PROJECT" 2>/dev/null || true
