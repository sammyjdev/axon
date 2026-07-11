from __future__ import annotations

import tomllib
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from axon.config.runtime import _load_toml_runtime_overrides


@settings(deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(malformed_text=st.text().filter(lambda t: t and not _is_valid_toml(t)))
def test_malformed_toml_handling(malformed_text: str, monkeypatch, tmp_path: Path) -> None:
    """Verify that malformed TOML is handled safely.

    The function should either raise tomllib.TOMLDecodeError (clear, typed error)
    or return an empty dict. It must NOT raise untyped exceptions like
    RecursionError or AttributeError.
    """
    config_path = tmp_path / "axon.toml"
    config_path.write_text(malformed_text, encoding="utf-8")
    monkeypatch.setenv("AXON_CONFIG", str(config_path))

    try:
        result = _load_toml_runtime_overrides()
        # If no exception is raised, result must be a valid empty dict
        assert result == {}
        assert isinstance(result, dict)
    except tomllib.TOMLDecodeError:
        # This is acceptable - a clear, typed error for invalid TOML
        pass
    except (RecursionError, AttributeError, TypeError, ValueError) as e:
        # Generic or unrelated exceptions are not acceptable
        raise AssertionError(
            f"Function raised untyped/unrelated exception {type(e).__name__}: {e}"
        ) from e


def _is_valid_toml(text: str) -> bool:
    """Check if text is valid TOML without raising an exception."""
    try:
        tomllib.loads(text)
        return True
    except Exception:
        return False


def test_unexpected_value_types_coerced_to_strings(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Verify that unexpected value types for allowed keys are safely handled.

    The function coerces all values to strings via str(). This is intentional
    per the return type dict[str, str]. Verify this coercion works and doesn't crash.
    """
    config_path = tmp_path / "axon.toml"
    toml_content = """[runtime]
mode = 123
engine_root = 45.67
vault_root = true
active_profile = ["a", "b", "c"]
vector_backend = { x = 1, y = 2 }
"""
    config_path.write_text(toml_content, encoding="utf-8")
    monkeypatch.setenv("AXON_CONFIG", str(config_path))

    result = _load_toml_runtime_overrides()

    # All returned values must be strings
    assert isinstance(result, dict)
    for key, value in result.items():
        assert isinstance(value, str), f"Key {key} has non-string value {type(value)}: {value}"

    # Verify that unexpected types were coerced to strings
    assert result["mode"] == "123"
    assert result["engine_root"] == "45.67"
    assert result["vault_root"] == "True"
    assert result["active_profile"] == "['a', 'b', 'c']"
    assert result["vector_backend"] == "{'x': 1, 'y': 2}"


@settings(
    deadline=None,
    max_examples=10,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(depth=st.integers(min_value=10, max_value=100))
def test_large_deeply_nested_toml_no_crash(
    depth: int,
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Verify that deeply nested TOML doesn't cause uncontrolled crashes.

    Generate TOML with many nested tables and assert:
    - No RecursionError or similar uncontrolled crash
    - Function completes in reasonable time
    """
    toml_lines = []
    current_path = []

    # Generate nested tables
    for i in range(depth):
        current_path.append(f"level{i}")
        section_name = ".".join(current_path)
        toml_lines.append(f"[{section_name}]")
        toml_lines.append(f'value{i} = "nested_{i}"')

    # Add a runtime section within the nested structure
    toml_lines.append("[runtime]")
    toml_lines.append('mode = "hybrid-local"')
    toml_lines.append('engine_root = "/tmp/engine"')

    config_path = tmp_path / "axon.toml"
    toml_content = "\n".join(toml_lines)
    config_path.write_text(toml_content, encoding="utf-8")
    monkeypatch.setenv("AXON_CONFIG", str(config_path))

    result = _load_toml_runtime_overrides()

    # Must succeed and return the expected values
    assert isinstance(result, dict)
    assert result.get("mode") == "hybrid-local"
    assert result.get("engine_root") == "/tmp/engine"


@settings(
    deadline=None,
    max_examples=5,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(size=st.integers(min_value=1000, max_value=100000))
def test_very_large_string_value_no_crash(
    size: int,
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Verify that very large string values don't cause uncontrolled crashes."""
    large_string = "x" * size
    config_path = tmp_path / "axon.toml"
    # Use toml escaping for the large string
    toml_content = f'[runtime]\nengine_root = "{large_string}"\n'
    config_path.write_text(toml_content, encoding="utf-8")
    monkeypatch.setenv("AXON_CONFIG", str(config_path))

    result = _load_toml_runtime_overrides()

    assert isinstance(result, dict)
    assert result.get("engine_root") == large_string


@settings(deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    unknown_key=st.text(
        alphabet="abcdefghijklmnopqrstuvwxyz_",
        min_size=1,
        max_size=20,
    ).filter(lambda k: k not in [
        "mode", "engine_root", "vault_root", "active_profile", "vector_backend",
        "fileindex_backend", "graph_backend", "decisions_backend", "sessions_backend",
        "db_backend",
    ]),
    value=st.text(
        alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
        min_size=0,
        max_size=50,
    ),
)
def test_unknown_keys_are_filtered_out(
    unknown_key: str,
    value: str,
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Verify that unknown keys in the runtime section are filtered out.

    Only allowed keys should appear in the returned dict.
    """
    config_path = tmp_path / "axon.toml"
    toml_content = f'[runtime]\n{unknown_key} = "{value}"\nmode = "hybrid-local"\n'
    config_path.write_text(toml_content, encoding="utf-8")
    monkeypatch.setenv("AXON_CONFIG", str(config_path))

    result = _load_toml_runtime_overrides()

    # The unknown key must NOT be in the result
    assert unknown_key not in result
    # But known keys should be present
    assert result.get("mode") == "hybrid-local"
