from __future__ import annotations

import re

# Redaction criteria according to dec-136
CREDENTIAL_KEY_PATTERN = re.compile(
    r"^(password|api_key|token|secret|authorization)$",
    re.IGNORECASE,
)
ENV_VAR_PATTERN = re.compile(r"^[A-Z_][A-Z0-9_]*$")
ENCRYPTED_KEY_PATTERN = re.compile(r"encrypted_content|reasoning_encrypted", re.IGNORECASE)


# Credential shapes, redacted where they appear inside a value so a message
# that mentions a key keeps its prose. Provider prefixes follow each vendor's
# published format; the length floors keep prose like "sk-learn" untouched.
_CREDENTIAL_RES = (
    re.compile(r"Bearer \S+"),
    re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"(?<![A-Za-z0-9])gsk_[A-Za-z0-9]{20,}"),
    re.compile(r"(?<![A-Za-z0-9])(?:ghp|gho|ghs|ghu)_[A-Za-z0-9]{30,}"),
    re.compile(r"(?<![A-Za-z0-9])github_pat_[A-Za-z0-9_]{30,}"),
    re.compile(r"(?<![A-Za-z0-9])xox[a-z](?:\.[a-z]+)?-[A-Za-z0-9-]{10,}"),
    re.compile(r"(?<![A-Za-z0-9.])xapp-[A-Za-z0-9-]{10,}"),
    re.compile(r"(?<![A-Za-z0-9])AKIA[0-9A-Z]{16}"),
    re.compile(r"(?<![A-Za-z0-9])AIza[0-9A-Za-z_-]{35}"),
    re.compile(r"-----BEGIN [A-Z ]*-----[\s\S]*?(?:-----END [A-Z ]*-----|$)"),
)
# NAME=value with a secret-sounding NAME: only the value goes. A `$VAR`
# reference is not a secret and stays readable.
_ASSIGNMENT_RE = re.compile(
    r"(?<![A-Za-z0-9])((?:[A-Z0-9_]*_(?:KEY|TOKEN|SECRET))|PASSWORD)=(?!\$[A-Za-z_{])(?:"
    # \"...\" : a value quoted inside a JSON-encoded string (tool arguments),
    # where an escaped quote inside the value reads \\\".
    r'\\"(?:\\\\\\"|\\\\\\\\|\\[^"]|[^\\])*?\\"'
    r'|\$?"(?:\\.|[^"\\])*"'
    r"|\$?'[^']*'"
    r"|[^\s'\"]+)"
)
REDACTED = "[REDACTED:credential]"


def redact_credentials(val: str) -> str:
    """`val` with every credential-shaped substring replaced."""
    for pattern in _CREDENTIAL_RES:
        val = pattern.sub(REDACTED, val)
    return _ASSIGNMENT_RE.sub(lambda m: f"{m.group(1)}={REDACTED}", val)


def is_credential_value(val: str) -> bool:
    """Check if a string matches common secret shapes."""
    return redact_credentials(val) != val


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
            # Postgres text and jsonb cannot hold NUL; tool output sometimes has it.
            val = val.replace("\x00", "")
            cleaned = redact_credentials(val)
            if cleaned != val:
                redactions.append(f"redacted credential-shaped value in: {key}")
            return cleaned

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
