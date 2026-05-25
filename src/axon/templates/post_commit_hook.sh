#!/usr/bin/env bash
# AXON ADR inference hook
# Installed by: pb adr hook install

PROJECT=$(basename "$(git rev-parse --show-toplevel)")
COMMIT_MSG=$(git log -1 --pretty=%s)

# Skip low-signal commit types
case "$COMMIT_MSG" in
  chore:*|docs:*|style:*|test:*|ci:*|build:*)
    exit 0
    ;;
esac

# Run ADR inference (non-blocking: errors don't fail the commit)
pb adr infer-commit --project "$PROJECT" 2>/dev/null || true
