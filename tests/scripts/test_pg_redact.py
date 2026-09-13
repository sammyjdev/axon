"""`redact_pg_url` exists to keep a password out of stderr and CI logs.

Two shapes got past it: a password carried as a query parameter, which asyncpg
accepts, and a DSN whose scheme is missing, where the parser found no netloc and
the function returned the input verbatim. A redactor that returns its input on
the paths it does not understand fails open, which is the one thing it must not do.
"""

from __future__ import annotations

from scripts.pg_redact import redact_pg_url


def test_a_password_in_the_userinfo_is_stripped() -> None:
    assert redact_pg_url("postgresql://axon:secret@127.0.0.1:5433/axon") == (
        "postgresql://axon@127.0.0.1:5433/axon"
    )


def test_a_password_in_a_query_parameter_is_stripped() -> None:
    out = redact_pg_url("postgresql://axon@127.0.0.1:5433/axon?password=S3cret&sslmode=require")
    assert "S3cret" not in out, out
    assert "sslmode=require" in out, "a harmless parameter must survive"


def test_a_dsn_the_parser_cannot_read_is_not_echoed_back() -> None:
    """No scheme, so urlsplit finds no netloc. Fail closed, never echo the input."""
    out = redact_pg_url("axon:secret@127.0.0.1:5433/axon")
    assert "secret" not in out, out
