from __future__ import annotations

import json

from axon.activity.sanitize import sanitize_event


def test_secret_fixture_never_survives_sanitization() -> None:
    raw = {
        "password": "my_super_secret_password",
        "nested": {
            "api_key": "sk-1234567890",  # gitleaks:allow - fixture, proves redaction
            "Authorization": "Bearer some_jwt_token",
            "some_aws_key": "AKIAIOSFODNN7EXAMPLE",
        },
        "safe_field": "hello world",
    }
    sanitized, redactions, coverage = sanitize_event(raw)
    
    assert coverage == "partial-observable"
    assert len(redactions) >= 4
    
    s = json.dumps(sanitized)
    assert "my_super_secret_password" not in s
    assert "sk-1234567890" not in s
    assert "Bearer some_jwt_token" not in s
    assert "AKIAIOSFODNN7EXAMPLE" not in s
    
    assert "hello world" in s
    assert sanitized["password"] == "[REDACTED:credential]"  # noqa: S105
    assert sanitized["nested"]["api_key"] == "[REDACTED:credential]"
    assert sanitized["nested"]["Authorization"] == "[REDACTED:credential]"
    assert sanitized["nested"]["some_aws_key"] == "[REDACTED:credential]"


def test_restricted_work_content_is_excluded_by_default() -> None:
    raw = {
        "ctx": "work",
        "some_data": 123,
    }
    sanitized, redactions, coverage = sanitize_event(raw)
    assert coverage == "redacted"
    assert sanitized == {"_excluded": "restricted-context"}
    assert "some_data" not in sanitized


def test_unknown_format_is_visible_not_silently_dropped() -> None:
    class Dummy:
        pass
    
    raw = {
        "known": "text",
        "unknown": Dummy(),
    }
    sanitized, redactions, coverage = sanitize_event(raw)
    
    assert coverage == "partial-observable"
    assert isinstance(sanitized["unknown"], Dummy)
    assert len(redactions) == 0


def test_resanitizing_is_byte_equivalent() -> None:
    raw = {
        "password": "abc",  # noqa: S105
        "nested": {
            "key": "Bearer def",
            "val": 123,
        }
    }
    s1, r1, c1 = sanitize_event(raw)
    s2, r2, c2 = sanitize_event(raw)
    
    assert s1 == s2
    assert r1 == r2
    assert c1 == c2


def test_environment_dump_exclusion() -> None:
    # more than 15 keys
    raw = {f"ENV_VAR_{i}": str(i) for i in range(20)}
    sanitized, redactions, coverage = sanitize_event(raw)
    assert coverage == "redacted"
    assert sanitized == {"_excluded": "environment-dump"}


def test_encrypted_reasoning_blob() -> None:
    raw = {
        "encrypted_content": "A" * 60
    }
    sanitized, redactions, coverage = sanitize_event(raw)
    assert coverage == "partial-observable"
    assert sanitized["encrypted_content"] == "[REDACTED:encrypted]"
