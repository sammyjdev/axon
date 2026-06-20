# dec-119 — Canonical activity/savings stores as the single source for live renderers (familiar, gain, dashboard)

- Status: accepted
- Date: 2026-06-20
- Relates to: dec-104 (event-driven, no idle cost), dec-109 (tool tracing and
  risk gating), dec-114 (doctor diagnostic). Supersedes the `pet/familiar.py`
  v3 prototype's signal sourcing.

## Context

AXON has three planned "show the engine working" surfaces:

- **familiar** — an ambient, reactive visual that lights up *while AXON indexes
  or while an agent (e.g. Claude) executes tools/workflows*.
- **`axon gain`** — an aggregate, `rtk gain`-style summary of compression
  savings (usage + visual impact in the terminal).
- **dashboard** (later) — a read-only web view over the same data.

The `pet/familiar.py` v3 prototype infers its `WORKING`/`AWAKE` state from a
**proxy signal** — TTY access-time changes via `ps` / `os.stat("/dev/<tty>")` —
and re-derives its counters by hand-reading `data/axon.db` and
`data/compression/stats.jsonl` (with a manual T-104 pollution filter). That is
macOS-specific, fragile, and divorced from what AXON actually does.

Meanwhile the **real signals already exist as canonical, file-backed stores**:

- **Activity / liveness** → `TraceStore` (`data_root/trace/records.jsonl`,
  append-only JSONL). Every tool call — MCP *and* CLI — appends stages
  (`invoke` → policy decision → result) through `@traced_tool` (dec-109),
  carrying `trace_id`, **risk class** (`read`/`write`/`destructive`), `caller`,
  `ctx`, and token estimates. "An agent is executing a workflow" is literally
  "records are landing in `records.jsonl`."
- **Savings** → `CompressionTelemetryStore` (`stats.jsonl`), already written on
  every compression with before/after tokens and engine label.
- **Decision counts** → the SQLite graph (`axon.db`).

These are *different* streams and must not be conflated: traces answer "what is
happening now", compression telemetry answers "how much was saved".

## Decision

1. **The canonical stores are the single source of truth for every live
   surface.** familiar, `gain`, and the future dashboard are **renderers** over
   `TraceStore` (activity), `CompressionTelemetryStore` (savings), and the
   SQLite graph (counts). No renderer invents a proxy signal (TTY atime) or
   re-implements filtering ad hoc.

2. **The familiar reacts to the activity stream, not the TTY.** Its `WORKING`
   state is driven by new records in `records.jsonl` (tail the append-only
   file by offset/rowid). The record's existing fields give the visual
   semantics for free:
   - tool call → a dendrite fires; **colour by `risk` class**
     (read / write / destructive);
   - **glow/intensity by token estimate** (and by savings from the telemetry
     store);
   - no records for N seconds → `AWAKE`.

3. **Indexing must emit activity into the same stream.** The `index` /
   `watch` paths currently do not emit granular progress; they will append
   activity stages to `TraceStore` so "while AXON indexes" is a first-class
   signal the familiar (and dashboard) can render, not a special case.

4. **Read-only, fire-and-forget, opt-in.** Renderers only *read* the stores;
   they never write back and never sit on a tool's hot path, so they add no
   latency and honour dec-104's no-idle-cost stance (tailing a file on demand is
   cheap; the familiar/dashboard are launched explicitly, e.g.
   `axon familiar --live`). Emission stays exactly where it is today — inside
   `@traced_tool` and the telemetry pipeline.

5. **Centralise the savings read + pollution filter.** The T-104 filter and
   p50/mean/saved aggregation move into one module (e.g.
   `observability/gain.py`) consumed by `gain`, the familiar's counters, and the
   dashboard — replacing the hand-rolled filter inside `familiar.py`.

6. **Drop the prototype's environment assumptions.** Source all paths from
   `RuntimeConfig` (as `TraceStore` already does via `data_root`); remove the
   hard-coded `/Users/samdev` paths and macOS-only TTY scanning. The familiar
   docstring already flags these as temporary.

## Consequences

- No new runtime dependency and no new transport. This is a sourcing/ownership
  decision: existing stores become the contract; renderers conform to it.
- `pet/familiar.py` is rebased onto `TraceStore` + the centralised savings
  module; its TTY/`ps` heuristic and hard-coded paths are removed.
- A small, shared `observability/gain.py` (or equivalent) is the seam reused by
  `axon gain`, the familiar, and the dashboard.
- The familiar remains a delight layer: opt-in, read-only, never required for
  capture or recall to function.

## Open follow-ups

- Emit indexing/watch progress stages into `TraceStore` (test-first).
- Confirm `TraceRecord` exposes the token/risk fields the renderers need; if a
  record field is missing for the glow semantics, add it at the
  `@traced_tool` emission point rather than re-deriving downstream.
- Decide the familiar's transport for "live": tail `records.jsonl` by byte
  offset vs. a lightweight watcher; either way it only reads.
- Sequencing (separate change sets, each test-first): (1) `observability/gain.py`
  + `axon gain`; (2) familiar rebased onto the stores; (3) indexing activity
  emission; (4) dashboard.
