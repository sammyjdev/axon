from __future__ import annotations

import re

# Redaction criteria according to dec-136
CREDENTIAL_KEY_PATTERN = re.compile(
    r"^(password|api_key|token|secret|authorization)$",
    re.IGNORECASE,
)
ENV_VAR_PATTERN = re.compile(r"^[A-Z_][A-Z0-9_]*$")
ENCRYPTED_KEY_PATTERN = re.compile(r"encrypted_content|reasoning_encrypted", re.IGNORECASE)


def is_credential_value(val: str) -> bool:
    """Check if a string matches common secret shapes."""
    if "Bearer " in val:
        return True
    if "AKIA" in val and len(val) >= 20:
        return True
    if "-----BEGIN " in val:
        return True
    return False


def sanitize_event(raw: dict[str, object]) -> tuple[dict[str, object], list[str], str]:
    """Sanitize an activity payload.

    Returns:
        (sanitized_content, redactions, coverage)
    """
    if not isinstance(raw, dict):
        return raw, [], "unsupported-format"

    # Restricted work context rejection
    if raw.get("ctx") == "work" or raw.get("context") == "work":
        return {"_excluded": "restricted-context"}, ["restricted context: work"], "redacted"

    # Check for environment dump
    env_keys = sum(
        1 for k in raw.keys() if isinstance(k, str) and ENV_VAR_PATTERN.match(k)
    )
    if env_keys > 15:
        return {"_excluded": "environment-dump"}, ["environment-dump"], "redacted"

    redactions: list[str] = []
    coverage_demoted = False

    def sanitize_val(key: str, val: object) -> object:
        nonlocal coverage_demoted

        # 1. Named secret fields
        if CREDENTIAL_KEY_PATTERN.search(key):
            redactions.append(f"redacted named secret field: {key}")
            return "[REDACTED:credential]"

        # 2. Encrypted / binary reasoning blobs
        if ENCRYPTED_KEY_PATTERN.search(key):
            if isinstance(val, str) and len(val) > 20:
                redactions.append(f"redacted encrypted/binary blob: {key}")
                return "[REDACTED:encrypted]"

        # 3. Values
        if isinstance(val, str):
            if is_credential_value(val):
                redactions.append(f"redacted credential-shaped value in: {key}")
                return "[REDACTED:credential]"
            return val

        if isinstance(val, dict):
            return {
                k: sanitize_val(str(k), v) for k, v in val.items()
            }

        if isinstance(val, list):
            return [sanitize_val(key, v) for v in val]

        if isinstance(val, (int, float, bool, type(None))):
            return val

        # Unknown formats are preserved but coverage demoted
        coverage_demoted = True
        return val

    sanitized = {str(k): sanitize_val(str(k), v) for k, v in raw.items()}

    if coverage_demoted:
        coverage = "partial-observable"
    elif redactions:
        coverage = "partial-observable"
    else:
        coverage = "complete-observable"

    return sanitized, redactions, coverage
