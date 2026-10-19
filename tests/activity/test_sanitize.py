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


# Fake keys are assembled at runtime so no secret scanner flags this file.
_FAKE = "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8S9t0"


def _redacted_text(value: str) -> str:
    sanitized, redactions, _ = sanitize_event({"text": f"use {value} now"})
    assert redactions == ["redacted credential-shaped value in: text"]
    return sanitized["text"]


def test_an_openai_style_key_inside_prose_is_redacted_and_the_prose_kept() -> None:
    assert _redacted_text("sk-" + "proj-" + _FAKE) == "use [REDACTED:credential] now"


def test_a_groq_key_is_redacted() -> None:
    assert _redacted_text("gsk_" + _FAKE) == "use [REDACTED:credential] now"


def test_a_github_classic_token_is_redacted() -> None:
    assert _redacted_text("ghp_" + _FAKE) == "use [REDACTED:credential] now"


def test_a_github_fine_grained_token_is_redacted() -> None:
    assert _redacted_text("github_pat_" + _FAKE) == "use [REDACTED:credential] now"


def test_a_slack_token_is_redacted() -> None:
    assert _redacted_text("xoxb-" + "1234-" + _FAKE) == "use [REDACTED:credential] now"


def test_an_aws_access_key_id_is_redacted() -> None:
    assert _redacted_text("AKIA" + "IOSFODNN7EXAMPLE") == "use [REDACTED:credential] now"


def test_a_google_api_key_is_redacted() -> None:
    assert _redacted_text("AIza" + _FAKE[:35]) == "use [REDACTED:credential] now"


def test_a_pem_private_key_block_is_redacted() -> None:
    pem = "-----BEGIN " + "RSA PRIVATE KEY-----\nMIIEow\n-----END RSA PRIVATE KEY-----"
    assert _redacted_text(pem) == "use [REDACTED:credential] now"


def test_a_secret_named_assignment_keeps_its_name_and_loses_its_value() -> None:
    for name in ("DEEPINFRA_API_KEY", "GH_TOKEN", "CLIENT_SECRET", "PASSWORD"):
        assert _redacted_text(f"{name}=hunter2value") == (
            f"use {name}=[REDACTED:credential] now"
        )


def test_a_variable_reference_and_ordinary_words_are_not_redacted() -> None:
    raw = {"text": "API_KEY=$DEEPINFRA_API_KEY; pip install sk-learn; task-runner"}
    sanitized, redactions, _ = sanitize_event(raw)
    assert sanitized == raw
    assert redactions == []


def test_a_nul_character_is_dropped_because_postgres_cannot_store_it() -> None:
    sanitized, _, _ = sanitize_event({"output": "bin\x00ary"})
    assert sanitized["output"] == "binary"
