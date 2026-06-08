# AXON Metrics Manifest

Date computed: 2026-06-08

Source of truth: code and committed data files. Every figure below was recomputed
from the live repo and traces to a command output or a committed file. No figure
is invented, estimated, or marketing rounded. Benchmark (deterministic model) and
telemetry (live production data) are reported separately and never blended.

## Manifest

| metric | value | source_file | method/filter | n | date_computed |
|---|---|---|---|---|---|
| Token reduction (deterministic benchmark) | 52.3% | benchmarks/model.py | deterministic cost model: baseline 87000 vs AXON 41500 input tokens | 20 turns | 2026-06-08 |
| Baseline input tokens (benchmark) | 87000 | benchmarks/model.py | session_total mode=baseline | 20 turns | 2026-06-08 |
| AXON input tokens (benchmark) | 41500 | benchmarks/model.py | session_total mode=axon | 20 turns | 2026-06-08 |
| Compression p50 (live telemetry) | 57.5% | data/compression/stats.jsonl | reduction_pct > 0 (compression fired) | 10 of 336 | 2026-06-08 |
| Compression mean (live telemetry) | 58.2% | data/compression/stats.jsonl | reduction_pct > 0 | 10 of 336 | 2026-06-08 |
| Compression p95 (live telemetry) | 84.7% | data/compression/stats.jsonl | reduction_pct > 0 | 10 of 336 | 2026-06-08 |
| Compression max (live telemetry) | 91.6% | data/compression/stats.jsonl | reduction_pct > 0 | 10 of 336 | 2026-06-08 |
| Test suite | 851 passed, 0 failed, 1 skipped | pytest tests/ -q | full suite; requires untracked data/architectural_lexicon.txt present | 852 total | 2026-06-08 |
| ADRs | 13 | docs/ADR.md | count of ADR-NNN headers, ADR-001 to ADR-013 | 13 | 2026-06-08 |
| Decision records | 16 | docs/decisions/ | count of dec-*.md, dec-100 to dec-115 | 16 | 2026-06-08 |
| Chunks ingested (storage) | NOT AVAILABLE as a canonical metric | live Qdrant localhost:6333 | runtime state only, not a committed file; local index is known polluted | n/a | 2026-06-08 |
| Collections (storage) | NOT AVAILABLE as a canonical metric | live Qdrant localhost:6333 | runtime state only; live count is 5 (career, knowledge, personal, saas, work) | n/a | 2026-06-08 |
| Retrieval or end-to-end latency | NOT AVAILABLE | data/trace/records.jsonl | no duration field; ts deltas across stages yield p50 0.001s, which is logging time, not measured latency | n/a | 2026-06-08 |

## Benchmark assumptions (deterministic model)

From `benchmarks/model.py` `DEFAULT_SESSION`:

- turns: 20
- base_context: 1500 tokens
- growth_per_turn (baseline): 300 tokens
- recall_budget (AXON): 2000 tokens

This is an explicit cost model, not a live measurement. It does not run inference
or measure real token consumption (see benchmarks/README.md caveats).

## Telemetry filter (live production data)

- Source: `data/compression/stats.jsonl`, N=336 total records.
- Filter: `reduction_pct > 0` (the compression pipeline actually fired). This
  single filter already excludes every no-op: disabled engine, graph tools
  (get_graph_path, get_graph_neighbors), and rtk-only paths all record
  reduction_pct=0.
- Filtered set: n=10, all engine=caveman/phi3+rtk, before_tokens range 178 to 1371.
- The README prose says "inputs above ~180 tokens"; the actual minimum that fired
  is 178, which the tilde covers.

## Storage note

Live Qdrant (localhost:6333) currently holds 7061 points across 5 collections.
This is volatile per-machine runtime state, not a committed repo figure, and the
local index is known to be polluted (a .venv was indexed previously). It is not a
publishable metric and is recorded here only for transparency.

## Reproduction commands

```bash
# Token reduction, deterministic benchmark
python -c "from benchmarks.model import DEFAULT_SESSION, session_total, savings; \
print(session_total(DEFAULT_SESSION, mode='baseline'), \
session_total(DEFAULT_SESSION, mode='axon'), round(savings(DEFAULT_SESSION)*100,1))"
# -> 87000 41500 52.3

# Token reduction, live telemetry (documented reproduction)
python -m axon.observability.compression_telemetry
# -> count_total 336, count_compressed 10, p50 57.5, avg 58.2, p95 84.7, max 91.6

# Test suite
pytest tests/ -q
# -> 851 passed, 1 skipped

# ADR count
grep -c "^#\+ ADR-" docs/ADR.md            # 13
ls docs/decisions/dec-*.md | wc -l         # 16
```
