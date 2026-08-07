# Adoption diagnosis — why AXON is not called as often as it should be

Measured 2026-08-07. Written because the finding contradicts a metric this
project reports, and because the causes are not obvious from the code.

## The headline

AXON's retrieval quality is fine. Its **adoption** is not.

```
1451 code reads          vs   18 search_code calls    (local transcripts)
4.7 search_code calls/day     across 64 active days   (5801 traced calls)
6 sessions in 3 months        against 779 decisions
```

`recall.savings` reports ~83-90% saved per call. That number is real and also
misleading on its own: it describes the efficiency of an event that almost never
happens. Coverage — how often the tool is reached for at all — is the missing
denominator. See `src/axon/observability/coverage.py`.

## Cause 1: the tools are invisible at decision time

`mcp__axon__*` tools arrive **deferred** in Claude Code. The model must call
`ToolSearch` before `search_code` exists for it. So at the exact moment it
chooses between `Read` and `search_code`, the golden rule in `AXON.md` — "always
`search_code` before `read`" — is asking for a tool that is not on the menu.

This is not a discipline problem, and no amount of instruction fixes it. It also
leaves a cost asymmetry that survives any workaround: `ToolSearch` +
`search_code` is two calls against one `Read`.

Mitigation in place: `~/.claude/hooks/axon-search-first.py`, a PreToolUse(Read)
hook that injects the missing `ToolSearch` step once per session and emits the
opportunity log. It is machine-local (`~/.claude/`), NOT in this repo — which
means it is not backed up here and does not travel to another machine.

## Cause 2: what needs remembering does not happen

| mechanism | volume | trigger |
|---|---|---|
| decisions | 779 | git hook, automatic |
| capture | 370 | git hook, automatic |
| sessions | 6 | agent must call `axon_session_start`/`_end` |
| search_code | 302 | agent must remember |

The pattern is consistent: automatic capture works, ritual does not. Anything
that depends on an agent remembering a step, at scale, does not happen.

`~/.claude/axon/ROUTER.md` documents an "IL-2 SessionStart injection" option to
replace the session ritual with a hook. **It was never installed** — the
`SessionStart` entry in `~/.claude/settings.json` belongs to `codeisland`. The
`.axon/context.md` file is regenerated on every `axon_session_end` and read by
nobody.

## Cause 3: retrieval misses "where is X" questions

#45 fixed natural-language recall and was measured at 21/24 on the golden set in
`tests/benchmark/fixtures/retrieval_golden.json`. That result is real, and the
golden set is not representative of how an agent actually queries.

The golden set asks about a function's **action**:

    "como uma sessao de trabalho e persistida em disco"  -> session_save

Agents ask about **location**:

    "onde o risco de uma tool MCP e avaliado"  -> policy/core.py

Five location-style queries against the live index, 2026-08-07:

| query | expected | returned |
|---|---|---|
| onde o risco de uma tool MCP é avaliado | `policy/core.py` | PROJECT_OVERVIEW.md, dec-109.md, USAGE_GUIDE.md |
| como as migrations são aplicadas | `pg_migrations.py` | correct |
| onde o servidor http define as rotas | `http/app.py` | test_check_onboarding_drift.py, test_setup.py |
| como o chunker divide java | `chunker.py` | correct |
| onde fica a detecção de contexto por path | `context/detector.py` | server.py, TASKS.md, pb.py |

**2/5.** The failure mode is the #45 symptom returning under a different query
style: docs and tests out-rank code. Latency 0.8-2.8s per query.

This closes a loop that explains the low usage: the agent calls the tool, gets
docs instead of code, falls back to grep, and stops calling.

## Secondary observations

- `axon_health` is **228 of ~987** MCP calls (23%) — nearly a quarter of MCP
  traffic is a health check, not delivered value.
- `ctx=personal` has **108 queries against 6869 indexed chunks** — the most
  indexed context is the least queried. Indexing effort and query demand are not
  aligned.
- `ctx=work`: **32 queries**, despite an active work project.

## What NOT to do next

Do not tune ranking yet. The 2/5 above is five hand-written queries — the same
mistake that produced the 21/24: measuring with a sample that does not represent
use. The opportunity log now collects real queries; wait for
`reads_without_prior_search` vs `reads_after_search` to say whether the gap is
visibility or ranking, then act on that.

## Open items this diagnosis creates

1. **SessionStart injection was never installed** — one line in
   `~/.claude/settings.json`. Cheapest available fix for session capture.
2. **The collector hook lives only in `~/.claude/`** — not versioned, not backed
   up, machine-local. If it matters, it belongs in the repo.
3. **The deferred-tools cost asymmetry** is unaddressed and may not be
   addressable from this side.
4. **A location-style golden set** should be built from the real queries in
   `data/trace/records.jsonl` rather than written by hand.
