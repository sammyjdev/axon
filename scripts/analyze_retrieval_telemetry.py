#!/usr/bin/env python3
"""Aggregate the retrieval telemetry in the TraceStore jsonl to decide, from data,
whether the self-correcting retrieval loop is working and whether more retrieval
agency (multi-hop) would pay off.

The decision signal is NOT query topic (query text is SHA8-hashed by @traced_tool
by design) — it is the OUTCOME distribution already recorded per ask()/search_code:
  - task_type / strategy mix          -> is the workload lookup or compositional?
  - hit_count (empty / thin rate)      -> is first-pass retrieval healthy?
  - self_correct verdict/retried/gave_up -> does the ONE recovery hop help, and how
    often does it fail anyway (gave_up) — that failure set is what multi-hop targets.

Re-run this after a few weeks of post-bge-m3 traffic and watch: hit_count rising off
median 1, and gave_up concentrated in ARCHITECTURE/DEEP_REASONING. Only then does
multi-hop have an evidence-backed case.

Usage:
    python3 scripts/analyze_retrieval_telemetry.py [path/to/records.jsonl]
Default path: <AXON_DATA_ROOT or ./data>/trace/records.jsonl
"""
from __future__ import annotations

import collections
import json
import os
import statistics as st
import sys


def _default_path() -> str:
    root = os.environ.get("AXON_DATA_ROOT", "data")
    return os.path.join(root, "trace", "records.jsonl")


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else _default_path()
    if not os.path.exists(path):
        print(f"no trace file at {path}", file=sys.stderr)
        return 1

    recs = [json.loads(line) for line in open(path, encoding="utf-8") if line.strip()]
    if not recs:
        print("trace file is empty")
        return 0

    def payload(r: dict) -> dict:
        return r.get("payload") or {}

    ts = sorted(r["ts"] for r in recs if r.get("ts"))
    print(f"records: {len(recs)}   span: {ts[0]} -> {ts[-1]}" if ts else f"records: {len(recs)}")
    print("stages:", dict(collections.Counter(r.get("stage") for r in recs)))

    ret = [r for r in recs if r.get("stage") == "retrieval"]
    print(f"\n== RETRIEVAL ({len(ret)}) ==")
    print("  task_type:", dict(collections.Counter(payload(r).get("task_type") for r in ret)))
    print("  strategy :", dict(collections.Counter(payload(r).get("strategy") for r in ret)))
    hc = [payload(r).get("hit_count") for r in ret if payload(r).get("hit_count") is not None]
    if hc:
        empty = sum(1 for x in hc if x == 0)
        thin = sum(1 for x in hc if 0 < x < 3)
        print(f"  hit_count: n={len(hc)} empty={empty} ({empty / len(hc) * 100:.1f}%) "
              f"thin(1-2)={thin} ({thin / len(hc) * 100:.1f}%) med={st.median(hc)} mean={st.mean(hc):.2f}")

    sc = [r for r in recs if r.get("stage") == "self_correct"]
    print(f"\n== SELF_CORRECT ({len(sc)}) ==")
    if not sc:
        print("  (no records — loop has not run in this telemetry window)")
    else:
        ps = [payload(r) for r in sc]
        n = len(ps)
        print("  verdict     :", dict(collections.Counter(p.get("verdict") for p in ps)))
        print("  strategy_used:", dict(collections.Counter(p.get("strategy_used") for p in ps)))
        retried = sum(1 for p in ps if p.get("retried"))
        gaveup = sum(1 for p in ps if p.get("gave_up"))
        print(f"  retried: {retried}/{n} ({retried / n * 100:.1f}%)   "
              f"gave_up: {gaveup}/{n} ({gaveup / n * 100:.1f}%)")
        rg = sum(1 for p in ps if p.get("retried") and p.get("gave_up"))
        if retried:
            print(f"  of retried, gave_up anyway: {rg}/{retried} ({rg / retried * 100:.1f}%) "
                  "<- the set multi-hop would target")
        # gave_up x task_type needs the retrieval stage of the same trace_id
        rt_task = {r.get("trace_id"): payload(r).get("task_type") for r in ret}
        gu_by_task = collections.Counter(
            rt_task.get(r.get("trace_id"), "UNKNOWN")
            for r in sc if payload(r).get("gave_up"))
        if gu_by_task:
            print("  gave_up by task_type:", dict(gu_by_task),
                  "<- multi-hop pays only if concentrated in ARCHITECTURE/DEEP_REASONING")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
