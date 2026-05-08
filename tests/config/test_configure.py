from __future__ import annotations

from pathlib import Path

from prometheus.config.runtime import recommend_profile, use_profile


def test_recommend_profile_prefers_privacy_first_for_restricted_data() -> None:
    profile, mode = recommend_profile(
        use_case="solo",
        privacy="restricted",
        hardware="cpu-only",
    )

    assert profile == "privacy-first"
    assert mode == "minimal"


def test_recommend_profile_prefers_team_dev_for_shared_team_setup() -> None:
    profile, mode = recommend_profile(
        use_case="team",
        privacy="internal",
        hardware="nvidia",
    )

    assert profile == "team-dev"
    assert mode == "remote-infra"


def test_recommend_profile_prefers_solo_dev_for_local_individual_setup() -> None:
    profile, mode = recommend_profile(
        use_case="solo",
        privacy="public",
        hardware="mac-laptop",
    )

    assert profile == "solo-dev"
    assert mode == "hybrid-local"


def test_use_profile_can_apply_recommended_profile(tmp_path: Path, monkeypatch) -> None:
    config_path = _write_config(tmp_path)
    monkeypatch.setenv("PROMETHEUS_CONFIG", str(config_path))

    profile, _mode = recommend_profile(
        use_case="team",
        privacy="internal",
        hardware="nvidia",
    )
    use_profile(profile)

    payload = config_path.read_text(encoding="utf-8")
    assert 'active_profile = "team-dev"' in payload
    assert 'mode = "remote-infra"' in payload


def _write_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "prometheus.toml"
    config_path.write_text(
        "\n".join(
            [
                "[runtime]",
                'mode = "hybrid-local"',
                'active_profile = "solo-dev"',
                f'engine_root = "{tmp_path / "engine"}"',
                f'vault_root = "{tmp_path / "vault"}"',
                "",
                "[profiles.solo-dev]",
                'description = "Single developer default"',
                'mode = "hybrid-local"',
                "",
                "[profiles.team-dev]",
                'description = "Shared team setup"',
                'mode = "remote-infra"',
                "",
                "[profiles.privacy-first]",
                'description = "Prefer local or remote self-hosted paths"',
                'mode = "minimal"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    return config_path
