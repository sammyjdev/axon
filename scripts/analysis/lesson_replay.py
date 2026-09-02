#!/usr/bin/env python3
"""Does the corpus answer questions that were actually asked?

Every delivery item in the plan assumes the bottleneck is delivery. That premise
was never tested: what nobody has measured is whether a lesson, once delivered,
would have helped. This script tests the corpus itself, with no channel in the
way.

The cases below are REAL mistakes with dates, each one made after the lesson
corpus already existed (first lesson 2026-08-22). The query is the situation as
it looked BEFORE the mistake - what an agent would have been able to ask at that
moment - never the mistake's own wording, which would be circular.

It reads through its own vector query rather than `LessonStore.search()`, on
purpose: `search()` credits `retrieved_count`, and an analysis harness must not
write into the delivery measurement it exists to inform.

    python3 scripts/analysis/lesson_replay.py [-k 5]

Labelling is manual and stays manual - `verdict` is filled in by a human reading
the returned lessons against `would_have_prevented`. An automatic label here
would be the author grading his own corpus.
"""
from __future__ import annotations

import argparse
import asyncio
import os

import asyncpg
from pgvector.asyncpg import register_vector

from axon.embedder.engine import EmbedderEngine
from axon.store.lessons import TABLE, resolve_lessons_dsn

# Real mistakes, all made on or after 2026-08-22, all verifiable in the
# transcripts. `query` is what the situation looked like beforehand.
# Two provenances, kept apart on purpose. HAND are cases the author chose, which
# invites the objection that he chose the ones that fail. SAMPLED are drawn from a
# seeded random sample of 255 self-correction moments mined from the transcripts
# since the corpus began, classified as real mistakes BEFORE any retrieval ran.
# They score the same (2/6 and 2/7), which is what retires the objection.
CASES = [
    {
        "provenance": "HAND",
        "when": "2026-09-01",
        "query": "I am counting how often a tool was used by scanning Claude Code "
                 "transcript files. I will select the files modified in the last 30 "
                 "days and count the events inside them.",
        "would_have_prevented": "Selecting FILES by mtime then counting EVENTS by "
                                "their own timestamp let events from outside the "
                                "window leak in. Made twice in one session; produced "
                                "'38 active days' inside a 30-day window.",
    },
    {
        "provenance": "HAND",
        "when": "2026-09-01",
        "query": "A new counter column was added with DEFAULT 0 and every row reads "
                 "0. I want to report what that says about how often the thing was "
                 "used before now.",
        "would_have_prevented": "Read 'all zeros' as a measured result when it was "
                                "the absence of measurement for the past. Went into "
                                "a commit message.",
    },
    {
        "provenance": "HAND",
        "when": "2026-09-01",
        "query": "I finished my change and I am about to commit. I will run git add "
                 "-A and then commit with my message.",
        "would_have_prevented": "The working tree already held unrelated untracked "
                                "files; `git add -A` swept 11 of them into the commit.",
    },
    {
        "provenance": "HAND",
        "when": "2026-09-01",
        "query": "I wrote a test for a guard I just added and it passes. I am ready "
                 "to call this covered.",
        "would_have_prevented": "The test passed with the guard deleted - it never "
                                "reached the guarded line. Only a mutation check "
                                "exposed it.",
    },
    {
        "provenance": "HAND",
        "when": "2026-09-01",
        "query": "I landed the fix, the suite is green and pipx install --force "
                 "succeeded. I want to confirm the behaviour changed on this machine.",
        "would_have_prevented": "The long-lived stdio MCP server kept the pre-install "
                                "code for the rest of the session, so a real capture "
                                "still truncated at the old cap.",
    },
    {
        "provenance": "HAND",
        "when": "2026-09-01",
        "query": "Two sibling code paths fixed the same lines for different reasons "
                 "and the rebase conflicted. I will keep both changes.",
        "would_have_prevented": "One guard had already moved into the shared callee, "
                                "so keeping both duplicated a check that could not be "
                                "reached unguarded.",
    },
    {
        "provenance": "SAMPLED",
        "when": "2026-08-27",
        "query": "My new tests pass and the suite is green. The change adds a few "
                 "guard clauses. I am ready to say this is covered.",
        "would_have_prevented": "Three mutants survived: the tests never exercised "
                                "the new guards.",
    },
    {
        "provenance": "SAMPLED",
        "when": "2026-09-01",
        "query": "I am adding a protection against a bad input. I will put the check "
                 "inside the method that uses the value.",
        "would_have_prevented": "The protection belonged in the caller; inside the "
                                "method it was reachable only after the damage.",
    },
    {
        "provenance": "SAMPLED",
        "when": "2026-09-01",
        "query": "I am writing a test that an instruction document states a property. "
                 "I will assert on the line that states it.",
        "would_have_prevented": "The property is about the whole instruction; "
                                "asserting one line passed while the meaning moved.",
    },
    {
        "provenance": "SAMPLED",
        "when": "2026-09-01",
        "query": "I gave an answer, the user pushed back, and I am about to revise "
                 "it. My first read was that the UI shows the first two linked items "
                 "in order.",
        "would_have_prevented": "The first read was right; the correction was the "
                                "error.",
    },
    {
        "provenance": "SAMPLED",
        "when": "2026-08-27",
        "query": "I am reading a packet dump and comparing the length field the tool "
                 "printed against the bytes I can see in the hex.",
        "would_have_prevented": "The printed length included the protocol header, so "
                                "the arithmetic was off and a whole diagnosis with it.",
    },
    {
        "provenance": "SAMPLED",
        "when": "2026-08-31",
        "query": "I wrote a warning that fires when two entries look ambiguous. It "
                 "compares their basenames inside the test fixture.",
        "would_have_prevented": "It measured the fixture, not the real ambiguity the "
                                "warning claimed to detect.",
    },
]



async def run(k: int) -> int:
    embedder = EmbedderEngine()
    con = await asyncpg.connect(resolve_lessons_dsn())
    await register_vector(con)
    try:
        for i, case in enumerate(CASES, 1):
            vec = embedder.embed_one(case["query"])
            rows = await con.fetch(
                f"SELECT kind, triggers, mistake, fix, 1 - (vector <=> $1) AS sim "  # noqa: S608
                f"FROM {TABLE} WHERE vector IS NOT NULL "
                f"ORDER BY vector <=> $1 LIMIT {int(k)}",
                vec,
            )
            print(f"\n{'='*78}\nCASE {i} ({case['when']})")
            print(f"query: {case['query']}")
            print(f"what actually went wrong: {case['would_have_prevented']}")
            print(f"\ntop-{k} returned:")
            for r in rows:
                print(f"  [{r['sim']:.3f}] ({','.join(r['triggers'][:3])}) "
                      f"{r['mistake'][:110]}")
            print("\n  verdict: __ (HIT = one of these would have prevented it / MISS)")
    finally:
        await con.close()
    print(f"\n{'='*78}\n{len(CASES)} cases. Labels are filled in by hand, on purpose.")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("-k", type=int, default=5)
    raise SystemExit(asyncio.run(run(ap.parse_args().k)))