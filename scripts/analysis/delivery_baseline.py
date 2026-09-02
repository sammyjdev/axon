#!/usr/bin/env python3
"""Re-derive every number dec-132 quotes, from the Claude Code transcripts.

Committed because the ADR's first two drafts quoted figures nobody could
reproduce - and two of them were wrong in the same way. Both bugs came from
selecting FILES by mtime and then counting EVENTS by their own timestamp, so
events older than the window leaked in from long-lived transcripts. That is how
"1890 prompts across 38 active days" fitted inside a 30-day window, and how a
21-day window got compared against a 7-day one.

Rule this script follows everywhere: the window filter applies to the EVENT.

    python3 scripts/analysis/delivery_baseline.py [--days 30]

Nothing here writes; it only reads ~/.claude/projects.
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import math
import os
import re
from collections import Counter, defaultdict

ROOT = os.path.expanduser("~/.claude/projects")
CODE_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".jsx", ".rs", ".java", ".go", ".rb", ".sh"}
PARENT_RE = re.compile(r"/([0-9a-f-]{36})/subagents/")


def wilson(k: int, n: int) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    z = 1.96
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, centre - half), min(1.0, centre + half))


def events(path: str):
    try:
        with open(path, errors="replace") as fh:
            for line in fh:
                try:
                    yield json.loads(line)
                except ValueError:
                    continue
    except OSError:
        return


def tool_uses(rec):
    content = (rec.get("message") or {}).get("content")
    if not isinstance(content, list):
        return
    for item in content:
        if isinstance(item, dict) and item.get("type") == "tool_use":
            yield item


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    args = ap.parse_args()
    cut = (dt.datetime.now() - dt.timedelta(days=args.days)).strftime("%Y-%m-%d")

    prompts_per_day: Counter[str] = Counter()
    main_sessions: set[str] = set()
    main_hits: set[str] = set()
    sub_sessions: set[str] = set()
    sub_hits: set[str] = set()
    parents: dict[str, list[int]] = defaultdict(lambda: [0, 0])

    for path in glob.glob(os.path.join(ROOT, "**", "*.jsonl"), recursive=True):
        is_sub = "/subagents/" in path
        in_window = False
        called = False
        for rec in events(path):
            ts = (rec.get("timestamp") or "")[:10]
            if not ts or ts < cut:
                continue
            in_window = True
            if not is_sub and rec.get("type") == "user":
                content = (rec.get("message") or {}).get("content")
                if isinstance(content, str):
                    text = content
                elif isinstance(content, list):
                    text = " ".join(
                        i.get("text", "") for i in content
                        if isinstance(i, dict) and i.get("type") == "text"
                    )
                else:
                    text = ""
                # A tool_result envelope and an injected reminder are not typed turns.
                if text.strip() and not text.startswith("<") \
                        and "system-reminder" not in text[:60]:
                    prompts_per_day[ts] += 1
            for use in tool_uses(rec):
                if use.get("name") == "mcp__axon__axon_search_lessons":
                    called = True
        if not in_window:
            continue
        if is_sub:
            sub_sessions.add(path)
            match = PARENT_RE.search(path)
            key = match.group(1) if match else path
            parents[key][0] += 1
            if called:
                sub_hits.add(path)
                parents[key][1] += 1
        else:
            main_sessions.add(path)
            if called:
                main_hits.add(path)

    print(f"window: events on or after {cut} ({args.days} days)\n")
    total = sum(prompts_per_day.values())
    dates = sorted(prompts_per_day)
    counts = sorted(prompts_per_day.values())
    print(f"human prompts (main sessions): {total} across {len(dates)} active dates")
    print(f"  median {counts[len(counts)//2]}/active day, max {counts[-1]}")
    print(f"  {'ERROR' if len(dates) > args.days + 1 else 'ok'}: "
          f"{len(dates)} active dates in a {args.days}-day window")

    print("\nsearch_lessons, by session:")
    for label, hits, sess in (("main", main_hits, main_sessions),
                              ("subagent", sub_hits, sub_sessions)):
        lo, hi = wilson(len(hits), len(sess))
        pct = len(hits) / len(sess) * 100 if sess else 0
        print(f"  {label:9s} {len(hits):3d}/{len(sess):4d} = {pct:5.1f}%  "
              f"95% CI [{lo*100:.1f}%, {hi*100:.1f}%]")

    # Subagents are dispatched in batches, so they are not independent trials.
    # The parent session is the unit; without this the p-value is inflated.
    hit_parents = sum(1 for v in parents.values() if v[1] > 0)
    lo, hi = wilson(hit_parents, len(parents))
    sizes = sorted((v[0] for v in parents.values()), reverse=True)
    print(f"\nclustered on the independent unit (parent session):")
    print(f"  parents dispatching subagents: {len(parents)} "
          f"(largest dispatches {sizes[0]} of {sum(sizes)})")
    print(f"  parents with >=1 call: {hit_parents}/{len(parents)} = "
          f"{hit_parents/len(parents)*100:.1f}%  95% CI [{lo*100:.1f}%, {hi*100:.1f}%]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
