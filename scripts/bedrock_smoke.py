"""One-shot AWS Bedrock Converse smoke check for the AXON bedrock backend.

Proves the raw Bedrock runtime path (boto3 -> Converse API) independently of
the router funnel, so a live failure can be attributed to AWS setup vs AXON
wiring. Reads the same env contract as the backend:

    AXON_BEDROCK_PROFILE  (optional, boto3 falls back to the default chain)
    AXON_BEDROCK_REGION   (default us-east-1)
    AXON_BEDROCK_MODEL_ID (default Claude Haiku 4.5 us inference profile)

Usage: python scripts/bedrock_smoke.py [prompt]
"""

from __future__ import annotations

import os
import sys
import time

import boto3

DEFAULT_MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"


def main() -> int:
    profile = os.environ.get("AXON_BEDROCK_PROFILE")
    region = os.environ.get("AXON_BEDROCK_REGION", "us-east-1")
    model_id = os.environ.get("AXON_BEDROCK_MODEL_ID", DEFAULT_MODEL_ID)
    prompt = sys.argv[1] if len(sys.argv) > 1 else "Reply with exactly: bedrock-ok"

    session = boto3.Session(profile_name=profile) if profile else boto3.Session()
    client = session.client("bedrock-runtime", region_name=region)

    started = time.perf_counter()
    response = client.converse(
        modelId=model_id,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig={"maxTokens": 64, "temperature": 0.0},
    )
    elapsed_ms = (time.perf_counter() - started) * 1000

    text = response["output"]["message"]["content"][0]["text"]
    usage = response["usage"]
    print(f"model     : {model_id}")
    print(f"region    : {region} (profile: {profile or 'default chain'})")
    print(f"reply     : {text}")
    print(f"tokens    : in={usage['inputTokens']} out={usage['outputTokens']}")
    print(f"latency   : {elapsed_ms:.0f} ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
