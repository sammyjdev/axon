# AXON Metrics Manifest

Current measured claims only. Each figure traces to a command output or a committed file, with the date it was computed. No figure is invented, estimated or rounded for marketing.

Retired and historical figures (dec-138 and earlier) were removed; the full former manifest is in the git history: `git show f898c82d:docs/METRICS.md`.

## Manifest

| metric | value | source_file | method/filter | n | date_computed |
|---|---|---|---|---|---|
| Lesson search, expected lesson ranked first on trigger queries | 41/41 with full-text search; about 5 ms per search | docs/decisions/dec-138-stop-code-vectors.md | Postgres full-text search over the 41 real lessons, one trigger query per lesson | 41 | 2026-10-15 |
| Test suite | 1528 passed, 0 failed, 3 skipped | python3 -m pytest -q (CI full-suite job) | summary line of the CI full-suite job on the PR head | 1531 | 2026-11-27 |
| ADRs | 13 | docs/ADR.md | count of ADR-NNN headers, ADR-001 to ADR-013 | 13 | 2026-06-08 |
| Decision records | 37 | docs/decisions/ | count of dec-*.md, dec-100 to dec-138 | 37 | 2026-10-30 |
| Retrieval or end-to-end latency | NOT AVAILABLE | data/trace/records.jsonl (local, not committed) | no duration field; ts deltas across stages yield p50 0.001s, which is logging time, not measured latency | n/a | 2026-06-08 |

## Reproduction commands

```bash
# Test suite (after: pip install -e ".[dev,http]", Docker running for the Postgres tests)
python3 -m pytest -q

# ADR and decision-record counts
grep -c "^#\+ ADR-" docs/ADR.md            # 13
ls docs/decisions/dec-*.md | wc -l         # 37
```
