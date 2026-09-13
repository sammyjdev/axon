### Task 0: make the live-write guard attribute writes to the test process

The guard Task 1 installed (`tests/conftest.py:71`,
`_the_suite_never_writes_to_the_operators_files`) fingerprints paths that belong to the
**machine**, not to the pytest process. `@traced_tool` appends to
`$AXON_ENGINE/data/trace/records.jsonl` on every MCP call, and the AXON git hooks write
there on every commit. Any agent loop that calls recall / search_code / record_outcome
around its gates therefore fails the gate and blames an innocent fixture. Measured
2026-09-13: exit 1 on the first baseline run naming `data/trace/records.jsonl`, then
2085 passed / exit 0 on a rerun with no concurrent MCP traffic, the file byte-identical
either way. Until this lands, every gate run in this repo is a coin flip.

  - Depends on: none; blocks 2, 3 and 5
  - Acceptance: a full suite run stays green while another process appends to
    `data/trace/records.jsonl` mid-run, and deleting the vault fixture from
    `tests/mcp/test_axon_tools.py::test_axon_handoff_includes_context` still makes the
    guard fail

