"""Tests for resolve_repo_key in axon.core.repo_identity."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from collections.abc import Generator
from pathlib import Path, PurePosixPath

import pytest

from axon.core.repo_identity import (
    REFUSED_RELATIVE,
    REFUSED_SCRATCH,
    REFUSED_TEST_RESIDUE,
    REFUSED_UNKNOWN,
    REFUSED_VAULT,
    RepoKeyResolution,
    resolve_repo_key,
)

FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent / "fixtures" / "dead_dirs_216.json"
)

KNOWN_NAMES: frozenset[str] = frozenset(
    {
        ".claude",
        "aerus-game-master-platform",
        "axon",
        "claude-usage-bar",
        "claude-usage-bar-rs",
        "glyph-kg",
        "gnomon-eval",
        "lina",
        "linkedin-content-manager",
        "lume",
        "merit",
        "orion-ai",
        "pharos",
        "pharos-backend",
        "pharos-frontend",
        "pitstop-os",
        "revvo",
        "rpg-master-ai",
        "rtkx",
    }
)

ALIASES: dict[str, str] = {
    "Pharos": "pharos",
    "PitStopOS": "pitstop-os",
    "Orion-AI": "orion-ai",
    "linkedin_content_manager": "linkedin-content-manager",
    "piloto_revvo": "revvo",
}


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(  # noqa: S603
        ["git", *args], cwd=cwd, check=True, capture_output=True  # noqa: S603, S607
    )


def _init_git_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(["init", "-b", "main"], cwd=path)
    _git(["config", "user.email", "test@axon.dev"], cwd=path)
    _git(["config", "user.name", "AXON Test"], cwd=path)
    return path


def _expand(p: str, fake_home: Path) -> Path:
    if p.startswith("~/"):
        return fake_home / p[2:]
    if p == "~":
        return fake_home
    return Path(p)


@pytest.fixture
def fake_home() -> Generator[Path, None, None]:
    with tempfile.TemporaryDirectory() as td:
        yield Path(td)


@pytest.fixture
def vault_root(fake_home: Path) -> Path:
    root = fake_home / "vault"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _load_fixture_entries() -> list[dict[str, str | None]]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_the_fixture_covers_every_row_of_the_analysis_table() -> None:
    entries = _load_fixture_entries()
    assert len(entries) == 216
    for entry in entries:
        assert set(entry.keys()) == {"dir", "key"}
        assert entry["dir"].startswith("~/")
    refused = [e["dir"] for e in entries if e["key"] is None]
    assert refused == [
        "~/dev/gnomon-eval-src",
        "~/vault/AXON/Decisions",
        "~/vault/work-notes",
    ]


@pytest.mark.parametrize("entry", _load_fixture_entries())
def test_every_dead_directory_resolves_to_its_documented_key(
    entry: dict[str, str | None],
    fake_home: Path,
    vault_root: Path,
) -> None:
    path = _expand(entry["dir"], fake_home)
    res = resolve_repo_key(
        path,
        known_names=KNOWN_NAMES,
        aliases=ALIASES,
        vault_root=vault_root,
    )
    assert isinstance(res, RepoKeyResolution)
    assert res.key == entry["key"]


def test_no_resolution_is_a_basename_guess(
    fake_home: Path,
    vault_root: Path,
) -> None:
    entries = _load_fixture_entries()
    for entry in entries:
        path = _expand(entry["dir"], fake_home)
        res = resolve_repo_key(
            path,
            known_names=KNOWN_NAMES,
            aliases=ALIASES,
            vault_root=vault_root,
        )
        assert res.key is None or res.key == entry["key"]
        basename = PurePosixPath(entry["dir"]).name
        if basename != entry["key"]:
            assert res.key != basename


def test_each_resolved_key_maps_to_exactly_one_repository_root(
    fake_home: Path,
    vault_root: Path,
) -> None:
    entries = _load_fixture_entries()
    reverse_aliases = {v: k for k, v in ALIASES.items()}
    roots_by_key: dict[str, set[str]] = {}

    for entry in entries:
        raw_dir = entry["dir"]
        path = _expand(raw_dir, fake_home)
        res = resolve_repo_key(
            path,
            known_names=KNOWN_NAMES,
            aliases=ALIASES,
            vault_root=vault_root,
        )
        if res.key is None:
            continue

        parts = PurePosixPath(raw_dir).parts
        matched_idx = -1
        target_tokens = {res.key}
        if res.key in reverse_aliases:
            target_tokens.add(reverse_aliases[res.key])

        for idx in range(len(parts) - 1, -1, -1):
            seg = parts[idx]
            if seg in target_tokens or (
                seg.endswith("-worktrees")
                and seg[:-len("-worktrees")] in target_tokens
            ):
                matched_idx = idx
                break

        assert matched_idx != -1
        root_prefix = "/".join(parts[: matched_idx + 1])
        roots_by_key.setdefault(res.key, set()).add(root_prefix)

    assert len(roots_by_key) == 15
    for key, roots in roots_by_key.items():
        assert len(roots) == 1, f"Key {key} resolved to multiple roots: {roots}"


def test_a_live_vault_directory_is_refused_and_the_reason_names_the_vault_root(
    fake_home: Path,
    vault_root: Path,
) -> None:
    _init_git_repo(vault_root)
    knowledge_dir = vault_root / "knowledge" / "x"
    knowledge_dir.mkdir(parents=True, exist_ok=True)

    res = resolve_repo_key(
        knowledge_dir,
        known_names=KNOWN_NAMES,
        aliases=ALIASES,
        vault_root=vault_root,
    )
    assert res.key is None
    assert res.reason is not None
    assert str(Path(os.path.realpath(vault_root))) in res.reason
    assert res.reason.startswith(REFUSED_VAULT)


def test_a_vault_directory_inside_a_live_repo_is_not_the_vault(
    fake_home: Path,
    vault_root: Path,
) -> None:
    repo_dir = fake_home / "dev" / "axon"
    _init_git_repo(repo_dir)
    til_dir = repo_dir / "src" / "axon" / "vault"
    til_dir.mkdir(parents=True, exist_ok=True)
    file_path = til_dir / "til_promoter.py"
    file_path.write_text("print('promoter')\n", encoding="utf-8")

    res = resolve_repo_key(
        til_dir,
        known_names=KNOWN_NAMES,
        aliases=ALIASES,
        vault_root=vault_root,
    )
    assert res.key == "axon"


def test_a_vault_file_inside_a_live_repo_keys_to_axon(
    fake_home: Path,
    vault_root: Path,
) -> None:
    repo_dir = fake_home / "dev" / "axon"
    _init_git_repo(repo_dir)
    til_dir = repo_dir / "src" / "axon" / "vault"
    til_dir.mkdir(parents=True, exist_ok=True)
    file_path = til_dir / "til_promoter.py"
    file_path.write_text("print('promoter')\n", encoding="utf-8")

    res = resolve_repo_key(
        file_path,
        known_names=KNOWN_NAMES,
        aliases=ALIASES,
        vault_root=vault_root,
    )
    assert res.key == "axon"


@pytest.mark.parametrize(
    "scratch_path",
    [
        "~/dev/_bench/x/y",
        "~/dev/_wt/scope-creep",
        "~/dev/_worktrees/a/b",
    ],
)
def test_scratch_roots_are_refused_whether_or_not_the_directory_exists(
    scratch_path: str,
    fake_home: Path,
    vault_root: Path,
) -> None:
    path = _expand(scratch_path, fake_home)
    res = resolve_repo_key(
        path,
        known_names=KNOWN_NAMES,
        aliases=ALIASES,
        vault_root=vault_root,
    )
    assert res.key is None
    assert res.reason is not None
    assert res.reason.startswith(REFUSED_SCRATCH)

    existing_scratch = fake_home / "_bench" / "maker-bench" / "arm"
    existing_scratch.mkdir(parents=True, exist_ok=True)
    res_existing = resolve_repo_key(
        existing_scratch,
        known_names=KNOWN_NAMES,
        aliases=ALIASES,
        vault_root=vault_root,
    )
    assert res_existing.key is None
    assert res_existing.reason is not None
    assert res_existing.reason.startswith(REFUSED_SCRATCH)


def test_no_refusal_path_probes_the_filesystem(
    fake_home: Path,
    vault_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _explode(*args: object, **kwargs: object) -> str:
        raise RuntimeError("repo_identity should not be called for refused paths")

    monkeypatch.setattr("axon.core.repo_identity.repo_identity", _explode)

    # Vault path (even if dir exists)
    _init_git_repo(vault_root)
    vault_dir = vault_root / "knowledge" / "x"
    vault_dir.mkdir(parents=True, exist_ok=True)
    res_vault = resolve_repo_key(
        vault_dir,
        known_names=KNOWN_NAMES,
        aliases=ALIASES,
        vault_root=vault_root,
    )
    assert res_vault.key is None
    assert res_vault.reason is not None
    assert res_vault.reason.startswith(REFUSED_VAULT)

    # Scratch path (even if dir exists)
    scratch_dir = fake_home / "dev" / "_bench" / "maker-bench"
    scratch_dir.mkdir(parents=True, exist_ok=True)
    res_scratch = resolve_repo_key(
        scratch_dir,
        known_names=KNOWN_NAMES,
        aliases=ALIASES,
        vault_root=vault_root,
    )
    assert res_scratch.key is None
    assert res_scratch.reason is not None
    assert res_scratch.reason.startswith(REFUSED_SCRATCH)

    # Test residue path
    res_tmp = resolve_repo_key(
        "/tmp/pytest-of-user/x",  # noqa: S108
        known_names=KNOWN_NAMES,
        aliases=ALIASES,
        vault_root=vault_root,
    )
    assert res_tmp.key is None
    assert res_tmp.reason is not None
    assert res_tmp.reason.startswith(REFUSED_TEST_RESIDUE)


def test_pytest_temp_roots_are_refused(
    vault_root: Path,
) -> None:
    res = resolve_repo_key(
        "/tmp/pytest-of-user/x",  # noqa: S108
        known_names=KNOWN_NAMES,
        aliases=ALIASES,
        vault_root=vault_root,
    )
    assert res.key is None
    assert res.reason is not None
    assert res.reason.startswith(REFUSED_TEST_RESIDUE)


def test_gnomon_eval_src_is_refused_not_prefix_matched(
    fake_home: Path,
    vault_root: Path,
) -> None:
    path = fake_home / "dev" / "gnomon-eval-src"
    res = resolve_repo_key(
        path,
        known_names=KNOWN_NAMES,
        aliases=ALIASES,
        vault_root=vault_root,
    )
    assert res.key is None
    assert res.reason == REFUSED_UNKNOWN


def test_exact_segment_match_does_not_let_claude_usage_bar_swallow_the_rs_repo(
    fake_home: Path,
    vault_root: Path,
) -> None:
    rs_path = fake_home / "dev" / "claude-usage-bar-rs" / "docs"
    res_rs = resolve_repo_key(
        rs_path,
        known_names=KNOWN_NAMES,
        aliases=ALIASES,
        vault_root=vault_root,
    )
    assert res_rs.key == "claude-usage-bar-rs"

    py_path = fake_home / "dev" / "claude-usage-bar" / "docs" / "superpowers" / "specs"
    res_py = resolve_repo_key(
        py_path,
        known_names=KNOWN_NAMES,
        aliases=ALIASES,
        vault_root=vault_root,
    )
    assert res_py.key == "claude-usage-bar"


def test_a_nested_repo_keys_to_itself(
    fake_home: Path,
    vault_root: Path,
) -> None:
    backend_path = fake_home / "dev" / "Pharos" / "pharos-backend" / "app"
    res_backend = resolve_repo_key(
        backend_path,
        known_names=KNOWN_NAMES,
        aliases=ALIASES,
        vault_root=vault_root,
    )
    assert res_backend.key == "pharos-backend"

    pharos_path = fake_home / "dev" / "Pharos" / "docs"
    res_pharos = resolve_repo_key(
        pharos_path,
        known_names=KNOWN_NAMES,
        aliases=ALIASES,
        vault_root=vault_root,
    )
    assert res_pharos.key == "pharos"


def test_an_alias_key_maps_to_its_value(
    fake_home: Path,
    vault_root: Path,
) -> None:
    cases = [
        (fake_home / "dev" / "Orion-AI" / "tests", "orion-ai"),
        (
            fake_home / "dev" / "linkedin_content_manager" / "agents",
            "linkedin-content-manager",
        ),
        (fake_home / "dev" / "piloto_revvo" / "src", "revvo"),
    ]
    for path, expected_key in cases:
        res = resolve_repo_key(
            path,
            known_names=KNOWN_NAMES,
            aliases=ALIASES,
            vault_root=vault_root,
        )
        assert res.key == expected_key


def test_the_session_repo_values_resolve_as_documented(
    fake_home: Path,
    vault_root: Path,
) -> None:
    axon_live = fake_home / "dev" / "axon"
    _init_git_repo(axon_live)

    cases: list[tuple[str | Path, str | None]] = [
        (fake_home / "dev" / "products" / "merit-worktrees" / "agent-issue-6", "merit"),
        (fake_home / "dev" / "_bench" / "maker-bench" / "gemini-3.7-flash-high__H3__r4", None),
        (fake_home / "dev" / "lina", "lina"),
        (fake_home / "dev" / "rpg-master-ai", "rpg-master-ai"),
        (axon_live, "axon"),
        ("axon", "axon"),
    ]
    for target, expected_key in cases:
        res = resolve_repo_key(
            target,
            known_names=KNOWN_NAMES,
            aliases=ALIASES,
            vault_root=vault_root,
        )
        assert res.key == expected_key


def test_a_multi_segment_relative_path_is_refused(
    vault_root: Path,
) -> None:
    res = resolve_repo_key(
        "src/axon/store",
        known_names=KNOWN_NAMES,
        aliases=ALIASES,
        vault_root=vault_root,
    )
    assert res.key is None
    assert res.reason == REFUSED_RELATIVE


def test_an_empty_known_names_set_refuses_rather_than_guesses(
    fake_home: Path,
    vault_root: Path,
) -> None:
    res = resolve_repo_key(
        fake_home / "dev" / "axon" / "src",
        known_names=frozenset(),
        aliases=ALIASES,
        vault_root=vault_root,
    )
    assert res.key is None
    assert res.reason == REFUSED_UNKNOWN


def test_unknown_repo_worktree_path_is_refused(
    fake_home: Path,
    vault_root: Path,
) -> None:
    path = fake_home / "dev" / "products" / "unknown-worktrees" / "agent-issue-6"
    res = resolve_repo_key(
        path,
        known_names=KNOWN_NAMES,
        aliases=ALIASES,
        vault_root=vault_root,
    )
    assert res.key is None
    assert res.reason == REFUSED_UNKNOWN


def test_a_live_pytest_of_directory_resolves_via_git_a_documented_deviation_from_r5(
    fake_home: Path,
    vault_root: Path,
) -> None:
    """R5 diverges from the brief here, deliberately, and this pins the divergence.

    The brief's step 1.2.0 says a `pytest-of-` segment is refused "whether or not the
    directory exists". It cannot be. `tmp_path` on this machine resolves under
    `/private/var/folders/.../T/pytest-of-<user>/`, so an unconditional rule refuses every
    row that `tests/scripts/test_rekey_embeddings_project.py` seeds. Measured: making the
    scan unconditional turns 4 of that file's 9 tests red, and that file may not be
    modified. The vault rule (R4) and the scratch rule (R8) keep the unconditional form;
    only R5 is guarded by `if not target.is_dir()`.

    The divergence costs nothing in scope: `embeddings` holds zero rows under a
    `pytest-of-*` path, and the two historical rows live only in `file_index`, which this
    task does not touch. The dead-path half of the rule is pinned by
    `test_pytest_temp_roots_are_refused`; this test pins the live half, so neither side can
    change without a test noticing.
    """
    pytest_dir = fake_home / "pytest-of-user" / "pytest-1" / "repo_a"
    _init_git_repo(pytest_dir)
    res = resolve_repo_key(
        pytest_dir,
        known_names=KNOWN_NAMES,
        aliases=ALIASES,
        vault_root=vault_root,
    )
    assert res.key == "repo_a"
    assert res.rule == "R0-live-git"
