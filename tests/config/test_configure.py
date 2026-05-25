from __future__ import annotations

from pathlib import Path

from axon.config.runtime import recommend_profile, select_capabilities, use_profile


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


def test_recommend_profile_respects_preferred_mode_override() -> None:
    profile, mode = recommend_profile(
        use_case="solo",
        privacy="public",
        hardware="mac-laptop",
        preferred_mode="remote-infra",
    )

    assert profile == "team-dev"
    assert mode == "remote-infra"


def test_recommend_profile_prefers_remote_infra_when_infra_override_is_remote() -> None:
    profile, mode = recommend_profile(
        use_case="solo",
        privacy="public",
        hardware="mac-laptop",
        infra="remote",
    )

    assert profile == "team-dev"
    assert mode == "remote-infra"


def test_recommend_profile_prefers_minimal_when_memory_override_is_light() -> None:
    profile, mode = recommend_profile(
        use_case="solo",
        privacy="public",
        hardware="cpu-only",
        memory="light",
    )

    assert profile == "privacy-first"
    assert mode == "minimal"


def test_use_profile_can_apply_recommended_profile(tmp_path: Path, monkeypatch) -> None:
    config_path = _write_config(tmp_path)
    monkeypatch.setenv("AXON_CONFIG", str(config_path))

    profile, _mode = recommend_profile(
        use_case="team",
        privacy="internal",
        hardware="nvidia",
    )
    use_profile(profile)

    payload = config_path.read_text(encoding="utf-8")
    assert 'active_profile = "team-dev"' in payload
    assert 'mode = "remote-infra"' in payload


def test_select_capabilities_for_privacy_first_light_profile() -> None:
    selection = select_capabilities(
        profile={
            "name": "privacy-first",
            "description": "Prefer local or remote self-hosted paths",
            "mode": "minimal",
            "cloud_policy": "deny",
            "infra_strategy": "local",
            "memory_tier": "light",
            "enabled_features": ("rtk", "local-rag"),
        }
    )

    assert selection.enabled_features == ("lean-context", "local-rag", "rtk")
    assert selection.overkill_features == (
        "cloud-routing",
        "heavy-local-models",
        "shared-remote-infra",
    )


def test_select_capabilities_for_team_remote_setup() -> None:
    selection = select_capabilities(
        use_case="team",
        privacy="internal",
        hardware="nvidia",
        infra="remote",
    )

    assert selection.enabled_features == ("shared-remote-infra",)
    assert selection.overkill_features == ("heavy-local-models", "offline-first")


def test_select_capabilities_for_full_local_high_capability_setup() -> None:
    selection = select_capabilities(
        use_case="solo",
        privacy="public",
        hardware="nvidia",
        preferred_mode="full-local",
        infra="local",
    )

    assert selection.enabled_features == (
        "heavy-local-models",
        "local-rag",
        "offline-first",
    )
    assert selection.overkill_features == ("shared-remote-infra",)


def _write_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "axon.toml"
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
