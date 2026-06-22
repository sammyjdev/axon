# axon.pet — familiar (dec-119 canonical sources)

**Status**: wired as `axon familiar` in the main CLI. Also runnable via
`python -m axon.pet`.

## What it is

A terminal companion that reacts to AXON's canonical activity stream in real
time:

- **Activity source**: tails `data_root/trace/records.jsonl` (TraceStore,
  dec-109/dec-119) by byte offset. A dendrite fires each time new trace
  records arrive — no TTY scanning, no `ps`, no macOS-specific atime checks.
- **Dendrites**: up to 8 compass-direction branches; fire colour is keyed to
  the record's `payload.risk` field (`read` → cyan, `write` → amber,
  `destructive` → red-orange).
- **Counters**: ADR count from SQLite (`data_root/axon.db`); tokens saved via
  `load_gain()` (centralised T-104 pollution filter, dec-119 §5).
- **Timeline**: recent ADR moments and compression moments (via
  `is_compression_record`).
- **State**: `WORKING` if any trace record arrived in the last 5 s; `HAPPY`
  when a new ADR appears; `AWAKE` otherwise.

## Run

```bash
axon familiar            # live loop, Ctrl+C to exit
axon familiar --frames N # bounded N-tick run (useful for CI or smoke-tests)
python -m axon.pet       # direct module invocation
```

All paths are resolved from `RuntimeConfig.data_root` — no hard-coded
filesystem paths.

## What changed from v3 prototype

- TTY/`ps` heuristic and `/dev/<tty>` atime polling removed.
- Hard-coded `/Users/samdev/dev/axon` paths removed; paths come from
  `load_runtime_config()`.
- `REAL_ENGINES` / `fetch_compression_data` hand-rolled filter replaced by
  `load_gain()` + `is_compression_record()` from `axon.observability.gain`.
- `axon familiar` wired in `src/axon/__main__.py`.
- Tests under `tests/pet/test_familiar.py`.

## What it relies on

- `axon.observability.trace_store.TraceStore` (file-backed JSONL).
- `axon.observability.gain.load_gain()` / `is_compression_record()`.
- Truecolor terminal (24-bit ANSI). All modern terminals support this.
- Read access to `data_root/axon.db` and `data_root/trace/records.jsonl`.

## Not in scope

- Sound / notification.
- Persistent mood that decays across runs.
- Multiple pet instances coordinating.
- Pet-driven actions (the familiar observes; it does not act).
