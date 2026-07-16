"""ADR-classifier model grid: NIM free-tier arms vs claude -p (plan quota).

Runs evaluate_adr_model (same one-shot harness production uses) over the
labeled fixture, k reps per arm, and appends one JSONL record per
(arm, rep). Timed-out calls are recorded as timeouts, never as failures
(forge benchmark rule).

Usage:
    python3 scripts/run_adr_model_grid.py [--reps 3] [--out results.jsonl]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "src"))

from axon.benchmark.model_eval import (  # noqa: E402
    ADREvalCase,
    evaluate_adr_model,
    make_litellm_adr_chat,
)

DEFAULT_FIXTURE = (
    REPO / "tests" / "benchmark" / "fixtures" / "adr_inference_golden.json"
)

NIM_ARMS = [
    "nvidia_nim/meta/llama-3.3-70b-instruct",  # current baseline
    "nvidia_nim/deepseek-ai/deepseek-v4-flash",
    "nvidia_nim/google/gemma-4-31b-it",
    "nvidia_nim/moonshotai/kimi-k2.6",
]

CLAUDE_ARM = "claude-plan/sonnet"
CALL_TIMEOUT_S = 120


def load_cases(fixture: Path = DEFAULT_FIXTURE) -> list[ADREvalCase]:
    raw = json.loads(fixture.read_text(encoding="utf-8"))
    return [
        ADREvalCase(
            commit_message=c["commit_message"],
            diff_summary=c["diff_summary"],
            expected=c["expected"],
            key_terms=tuple(c["key_terms"]),
        )
        for c in raw
    ]


def make_claude_plan_chat(timeouts: list[str]):
    """Plan-quota arm: dispatch through the claude CLI (subscription rail)."""

    def chat(model: str, prompt: str) -> str:
        try:
            proc = subprocess.run(  # noqa: S603
                ["claude", "-p", "--model", "sonnet"],  # noqa: S607
                input=prompt,
                capture_output=True,
                text=True,
                timeout=CALL_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired:
            timeouts.append(prompt[:80])
            return "__TIMEOUT__"
        return proc.stdout.strip()

    return chat


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reps", type=int, default=3)
    parser.add_argument("--out", type=Path, default=REPO / "adr-grid-results.jsonl")
    parser.add_argument("--arms", nargs="*", default=[*NIM_ARMS, CLAUDE_ARM])
    parser.add_argument(
        "--sleep-s", type=float, default=0.0,
        help="pause between calls (free-tier TPM pacing, e.g. groq)",
    )
    parser.add_argument(
        "--max-tokens", type=int, default=400,
        help="candidate production contract (400 = current production)",
    )
    parser.add_argument(
        "--tag", default="",
        help="suffix appended to the arm name in records, e.g. '@mt2000'",
    )
    parser.add_argument(
        "--template", type=Path, default=None,
        help="alternative prompt template (candidate contract); default = production",
    )
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    args = parser.parse_args()
    template = args.template.read_text(encoding="utf-8") if args.template else None

    cases = load_cases(args.fixture)
    litellm_chat = make_litellm_adr_chat(max_tokens=args.max_tokens)

    for arm in args.arms:
        for rep in range(1, args.reps + 1):
            timeouts: list[str] = []
            if arm == CLAUDE_ARM:
                chat = make_claude_plan_chat(timeouts)
            else:
                chat = litellm_chat
            if args.sleep_s:
                inner = chat

                def chat(model: str, prompt: str, _inner=inner) -> str:
                    import time

                    time.sleep(args.sleep_s)
                    return _inner(model, prompt)
            try:
                result = evaluate_adr_model(arm, cases, chat=chat, template=template)
            except Exception as exc:  # noqa: BLE001 — record, keep the grid going
                record = {"arm": arm + args.tag, "rep": rep, "error": str(exc)[:200]}
            else:
                record = {
                    "arm": arm + args.tag,
                    "rep": rep,
                    "duration_ms": round(result.duration_ms),
                    "timeouts": len(timeouts),
                    "checks": [
                        {"name": c.name, "passed": c.passed, "actual": c.actual}
                        for c in result.checks
                    ],
                }
            with args.out.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            print(f"{arm} rep{rep}: {record.get('error') or 'ok'}", flush=True)

    print(f"results -> {args.out}")


if __name__ == "__main__":
    main()
