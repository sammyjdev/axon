# tests/scripts/test_rekey_embeddings_project_planning.py
"""Planning tests for scripts/rekey_embeddings_project.py.

Verifies:
- RekeyPlan accounts for every row (changed + unchanged + refused == total).
- Unchanged rows are reported rather than dropped.
- Refused rows are absent from the change list and unchanged list.
- A refused row is never in the UPDATE list.
- write_refused emits TSV rows with id, path, and reason without tabs/newlines.
- The CLI keeps every existing flag and adds --refused-out.
- Dry run and apply modes both print the three counts and their sum.
- Missing router file exits non-zero without printing plan summaries.
- Empty known_names set exits non-zero without printing plan summaries.
- Missing projects.json degrades to an empty alias table and emits warning.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import pytest

from axon.core.repo_identity import REFUSED_UNKNOWN, resolve_repo_key
from scripts.rekey_embeddings_project import (
    PlannedRow,
    RefusedRow,
    RekeyPlan,
    ResolverInputs,
    apply_plan,
    format_plan_counts,
    load_aliases,
    load_known_names,
    parse_args,
    plan_rekey,
    read_router_text,
    run,
    write_refused,
)


def test_plan_rekey_accounts_for_every_row() -> None:
    with tempfile.TemporaryDirectory() as td:
        home = Path(td)
        vault_root = home / "vault"
        vault_root.mkdir(parents=True, exist_ok=True)
        known_names = frozenset({"glyph-kg", "axon"})
        aliases = {"Pharos": "pharos"}
        inputs = ResolverInputs(
            known_names=known_names,
            aliases=aliases,
            vault_root=vault_root,
        )

        rows = [
            ("emb-1", str(home / "dev" / "glyph-kg" / "docs" / "a.md"), "tests"),
            ("emb-2", str(home / "dev" / "glyph-kg" / "docs" / "b.md"), "glyph-kg"),
            ("emb-3", str(vault_root / "notes.md"), "tests"),
        ]
        plan = plan_rekey(rows, inputs=inputs)

        assert len(plan.changed) + len(plan.unchanged) + len(plan.refused) == len(rows)
        assert plan.total == len(rows)
        assert len(plan.unchanged) == 1
        assert plan.unchanged[0].id == "emb-2"
        assert plan.unchanged[0].new_project == "glyph-kg"


def test_unchanged_rows_are_reported_not_dropped() -> None:
    with tempfile.TemporaryDirectory() as td:
        home = Path(td)
        vault_root = home / "vault"
        vault_root.mkdir(parents=True, exist_ok=True)
        known_names = frozenset({"glyph-kg", "axon"})
        inputs = ResolverInputs(
            known_names=known_names,
            aliases={},
            vault_root=vault_root,
        )

        rows = [
            ("emb-2", str(home / "dev" / "glyph-kg" / "docs" / "b.md"), "glyph-kg"),
        ]
        plan = plan_rekey(rows, inputs=inputs)

        assert len(plan.changed) == 0
        assert len(plan.unchanged) == 1
        assert len(plan.refused) == 0
        assert plan.unchanged[0].id == "emb-2"
        assert plan.unchanged[0].project == "glyph-kg"
        assert plan.unchanged[0].new_project == "glyph-kg"


def test_a_refused_row_is_absent_from_the_change_list() -> None:
    with tempfile.TemporaryDirectory() as td:
        home = Path(td)
        vault_root = home / "vault"
        vault_root.mkdir(parents=True, exist_ok=True)
        known_names = frozenset({"glyph-kg", "axon"})
        aliases = {"Pharos": "pharos"}
        inputs = ResolverInputs(
            known_names=known_names,
            aliases=aliases,
            vault_root=vault_root,
        )

        rows = [
            ("emb-1", str(home / "dev" / "glyph-kg" / "docs" / "a.md"), "tests"),
            ("emb-2", str(home / "dev" / "glyph-kg" / "docs" / "b.md"), "glyph-kg"),
            ("emb-3", str(vault_root / "notes.md"), "tests"),
        ]
        plan = plan_rekey(rows, inputs=inputs)

        refused_ids = {r.id for r in plan.refused}
        changed_ids = {r.id for r in plan.changed}
        unchanged_ids = {r.id for r in plan.unchanged}

        assert "emb-3" in refused_ids
        assert "emb-3" not in changed_ids
        assert "emb-3" not in unchanged_ids


@pytest.mark.asyncio
async def test_the_update_list_is_built_only_from_changed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with tempfile.TemporaryDirectory() as td:
        home = Path(td)
        vault_root = home / "vault"
        vault_root.mkdir(parents=True, exist_ok=True)
        inputs = ResolverInputs(
            known_names=frozenset({"glyph-kg", "axon"}),
            aliases={},
            vault_root=vault_root,
        )

        fake_db_rows = [
            {
                "id": "emb-1",
                "file_path": str(home / "dev" / "glyph-kg" / "docs" / "a.md"),
                "project": "tests",
            },
            {
                "id": "emb-2",
                "file_path": str(home / "dev" / "glyph-kg" / "docs" / "b.md"),
                "project": "glyph-kg",
            },
            {
                "id": "emb-3",
                "file_path": str(vault_root / "notes.md"),
                "project": "tests",
            },
        ]

        executed_updates: list[tuple[str, str]] = []

        class FakeTransaction:
            async def __aenter__(self) -> FakeTransaction:
                return self

            async def __aexit__(self, *args: Any) -> None:
                pass

        class FakeConnection:
            async def fetch(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
                return fake_db_rows

            def transaction(self) -> FakeTransaction:
                return FakeTransaction()

            async def executemany(self, sql: str, updates: list[tuple[str, str]]) -> None:
                executed_updates.extend(updates)

            async def close(self) -> None:
                pass

        async def fake_connect(*args: Any, **kwargs: Any) -> FakeConnection:
            return FakeConnection()

        monkeypatch.setattr("asyncpg.connect", fake_connect)

        plan = await apply_plan(
            "postgresql://fake_user:fake_pw@localhost:5432/fake_db", inputs=inputs
        )

        assert len(plan.changed) == 1
        assert len(plan.unchanged) == 1
        assert len(plan.refused) == 1
        assert executed_updates == [("glyph-kg", "emb-1")]
        updated_ids = [uid for _, uid in executed_updates]
        assert "emb-2" not in updated_ids
        assert "emb-3" not in updated_ids


def test_refused_out_writes_id_tab_path_tab_reason(tmp_path: Path) -> None:
    vault_root = tmp_path / "vault"
    refused_rows = [
        RefusedRow(
            id="emb-3",
            file_path=str(vault_root / "notes.md"),
            reason=f"vault root {vault_root}",
        ),
        RefusedRow(
            id="emb-4",
            file_path="/fake/dev/_bench/maker-bench/arm",
            reason="scratch root _bench",
        ),
    ]
    plan = RekeyPlan(changed=[], unchanged=[], refused=refused_rows)
    tsv_path = tmp_path / "refused.tsv"
    write_refused(plan, tsv_path)

    lines = tsv_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    for line in lines:
        parts = line.split("\t")
        assert len(parts) == 3
        assert "\t" not in parts[0] and "\n" not in parts[0]
        assert "\t" not in parts[1] and "\n" not in parts[1]
        assert "\t" not in parts[2] and "\n" not in parts[2]
    assert str(vault_root) in lines[0].split("\t")[2]


def test_the_cli_keeps_every_existing_flag_and_adds_refused_out(tmp_path: Path) -> None:
    refused_file = str(tmp_path / "refused.tsv")
    args = parse_args(
        [
            "--apply",
            "--all",
            "--embeddings",
            "--only-project",
            "my_proj",
            "--pg-url",
            "postgresql://custom:5432/db",
            "--refused-out",
            refused_file,
        ]
    )
    assert args.apply is True
    assert args.all is True
    assert args.embeddings is True
    assert args.only_project == "my_proj"
    assert args.pg_url == "postgresql://custom:5432/db"
    assert args.refused_out == refused_file


def test_both_modes_print_the_three_counts_and_their_sum() -> None:
    plan = RekeyPlan(
        changed=[PlannedRow("1", "a", "old", "new")],
        unchanged=[PlannedRow("2", "b", "same", "same")],
        refused=[RefusedRow("3", "c", "refused reason")],
    )
    lines = format_plan_counts(plan)
    assert lines == [
        "changed 1",
        "unchanged 1",
        "refused 1",
        "total 3",
    ]


@pytest.mark.asyncio
async def test_a_missing_router_file_exits_non_zero_with_no_plan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("AXON_ROUTER_MD", str(tmp_path / "nope" / "ROUTER.md"))
    assert read_router_text() is None
    assert load_known_names(None) == frozenset()
    dead_dir = tmp_path / "dead_dir"
    rows = [("id-1", str(dead_dir / "f.py"), "old_proj")]

    async def fake_fetch_rows(*args: Any, **kwargs: Any) -> list[tuple[str, str, str]]:
        return rows

    monkeypatch.setattr("scripts.rekey_embeddings_project.fetch_rows", fake_fetch_rows)

    exit_code = await run([])
    assert exit_code != 0

    out, err = capsys.readouterr()
    assert "changed" not in out
    assert "unchanged" not in out
    assert "refused" not in out
    assert "would re-key" not in out
    assert "re-keyed" not in out
    assert "Error:" in err or "error" in err.lower()


@pytest.mark.asyncio
async def test_an_empty_known_names_set_exits_non_zero_with_no_plan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    router_file = tmp_path / "ROUTER.md"
    router_file.write_text("# ROUTER\nNo marker here\n", encoding="utf-8")
    monkeypatch.setenv("AXON_ROUTER_MD", str(router_file))
    router_text = read_router_text()
    assert router_text is not None
    assert load_known_names(router_text) == frozenset()

    dead_dir = tmp_path / "dead_dir"
    rows = [("id-1", str(dead_dir / "f.py"), "old_proj")]


    async def fake_fetch_rows(*args: Any, **kwargs: Any) -> list[tuple[str, str, str]]:
        return rows

    monkeypatch.setattr("scripts.rekey_embeddings_project.fetch_rows", fake_fetch_rows)

    exit_code = await run([])
    assert exit_code != 0

    out, err = capsys.readouterr()
    assert "changed" not in out
    assert "unchanged" not in out
    assert "refused" not in out
    assert "would re-key" not in out
    assert "re-keyed" not in out
    assert "Error:" in err or "error" in err.lower()


def test_a_missing_projects_json_degrades_to_an_empty_alias_table(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    missing_manifest = tmp_path / "missing_projects.json"
    aliases = load_aliases(missing_manifest)
    assert aliases == {}

    _, err = capsys.readouterr()
    assert "projects.json" in err

    with tempfile.TemporaryDirectory() as td:
        home = Path(td)
        vault_root = home / "vault"
        known = frozenset({".claude", "axon", "orion-ai"})
        res = resolve_repo_key(
            home / "dev" / "Orion-AI" / "docs",
            known_names=known,
            aliases=aliases,
            vault_root=vault_root,
        )
        assert res.key is None
        assert res.reason == REFUSED_UNKNOWN


def _refused_only_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """One row under the vault root: refused, never changed."""
    router_file = tmp_path / "ROUTER.md"
    router_file.write_text("**Onboarded repos (canonical):**\naxon\n\n", encoding="utf-8")
    monkeypatch.setenv("AXON_ROUTER_MD", str(router_file))
    vault_root = tmp_path / "vault"
    monkeypatch.setenv("AXON_VAULT", str(vault_root))
    rows = [("emb-1", str(vault_root / "notes.md"), "old_proj")]

    async def fake_fetch_rows(*args: Any, **kwargs: Any) -> list[tuple[str, str, str]]:
        return rows

    monkeypatch.setattr("scripts.rekey_embeddings_project.fetch_rows", fake_fetch_rows)


@pytest.mark.asyncio
async def test_an_unwritable_refused_out_stops_apply_before_any_db_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _refused_only_setup(tmp_path, monkeypatch)
    apply_calls: list[str] = []

    async def fake_apply_plan(*args: Any, **kwargs: Any) -> RekeyPlan:
        apply_calls.append("called")
        return RekeyPlan(changed=[], unchanged=[], refused=[])

    monkeypatch.setattr("scripts.rekey_embeddings_project.apply_plan", fake_apply_plan)
    blocker = tmp_path / "blocker.txt"
    blocker.write_text("not a directory", encoding="utf-8")
    unwritable = blocker / "refused.tsv"

    exit_code = await run(["--apply", "--all", "--refused-out", str(unwritable)])

    assert exit_code == 1
    assert apply_calls == []
    out, err = capsys.readouterr()
    assert "refused-out" in err
    assert "re-keyed" not in out


@pytest.mark.asyncio
async def test_an_unwritable_refused_out_fails_the_dry_run_cleanly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _refused_only_setup(tmp_path, monkeypatch)
    blocker = tmp_path / "blocker.txt"
    blocker.write_text("not a directory", encoding="utf-8")

    exit_code = await run(["--refused-out", str(blocker / "refused.tsv")])

    assert exit_code == 1
    _, err = capsys.readouterr()
    assert "refused-out" in err


@pytest.mark.asyncio
async def test_a_refused_only_plan_does_not_claim_no_rows_were_found(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _refused_only_setup(tmp_path, monkeypatch)

    exit_code = await run([])

    assert exit_code == 0
    out, _ = capsys.readouterr()
    assert "no matching embedding rows found" not in out
    assert "no embedding rows need re-keying" in out
    assert "refused 1" in out
