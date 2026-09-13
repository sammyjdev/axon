### Task 3: key sessions by repo_identity on write

`_detect_repo()` is already correct. What passes raw is the `repo` the **caller** sends,
and that happens at **eight** call sites in `src/axon/mcp/server.py`, not one, all of the
form `repo or _detect_repo()`: `axon_session_start` (1132), `axon_capture_event` (1172),
`axon_record_outcome` (1197), `axon_get_context` (1289), `axon_capture` (1305),
`axon_search` (1327), `axon_handoff` (1359), `axon_mark_done` (1416). The read paths
matter as much as the writes: a search with a raw absolute path finds nothing. One
resolver, eight one-line call sites.

  - Depends on: Task 0
  - Acceptance: `axon_session_start` invoked with cwd `/Users/samdev/dev/axon` stores
    `repo='axon'` and its returned recall is non-empty; a caller passing an absolute path
    at any of the eight sites is normalized to the repo name; a re-key script for the
    rows already holding absolute paths exists, dry-run by default. Applying it to the
    live store is the operator's run.

