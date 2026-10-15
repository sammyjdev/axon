# dec-137: remove rtkx (and the rtk command filter) from AXON and the machine

- Status: Accepted
- Date: 2026-10-15
- Supersedes: dec-118 (keep rtkx for reversible compression)
- Relates to: dec-116 (GLYPH owns retrieval), caveman compressor

## Decision

AXON no longer integrates rtkx. Removed: `src/axon/context/rtk.py`,
`rtk_bootstrap.py`, the `axon rtk`, `rtk-status`, `rtk-init`, `rtk-install`,
`rtk-proxy`, `run` and `git` commands, the `restore_context` MCP tool, the
opt-in reversible-compression store (`AXON_RTK_REVERSIBLE`), `AXON_RTK_BIN`,
the doctor presence lines, and the project-local `.rtk/filters.toml`.
Context compression is caveman only. `rtk_max_tokens` and
`AXON_RTK_MAX_TOKENS` keep their names (they budget the caveman stage) to avoid
breaking MCP clients; the response field `rtk_note` is now `quality_note`.
Historical telemetry tags (`rtk`, `rtkx`) stay recognised by `axon gain`.

## Why

1. **The stage compressed nothing.** AXON ran `rtkx read <file>`, whose default
   level is `none` (byte-identical output). In `data/compression/stats.jsonl`
   the rtkx-only rows (n=13) average 0.0 percent reduction, and
   `caveman/phi3+rtkx` (n=47) averages 2.8 percent, against 78.7 percent for the
   caveman stage before rtkx existed. The reduction is caveman's.
2. **The filter proxy has net-neutral value and real failure modes.** A/B runs
   of an agent with and without it (2026-10-15): all conditions completed every
   task, but on a 40-failure pytest task the filtered output showed 10 of 40
   failures and the agent needed 3.6 calls against 1.7 (17 percent higher
   cost). `git diff --name-only | wc -l` returned 4 instead of 1 because the
   filter duplicated file names and the hook still rewrote the pipe.
   `rtk grep -A22` cut 988 lines to 28. The agent bypassed the filter on 14
   percent of real calls. Measured token savings, once oversized outputs are
   excluded, were around 11 percent, and the dashboard headline (42 percent)
   was inflated by 73 reads counted against a near-zero recorded output.
3. **Licence drift upstream.** Upstream rtk 1.x moved its compression engine to
   BUSL-1.1, so tracking upstream would pull non-Apache code into the fork.

## Consequences

- Reversible compression and `restore_context` are gone. Nothing in the
  repository depended on them beyond tests, which were removed.
- `axon doctor` no longer reports rtkx. The rtkx repository is dropped from
  the onboarded-repo list on this machine.
- Revisit only with a measured, net-positive result on the A/B protocol above.
