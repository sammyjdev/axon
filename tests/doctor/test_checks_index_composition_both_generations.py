"""Tests for index composition check with both generations present."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator

import asyncpg
import pytest

from axon.doctor.checks.index_composition import check_index_composition


def _get_test_pg_url() -> str:
    env = os.environ.get("AXON_PG_URL")
    if env:
        return env
    from axon.config.runtime import load_runtime_config

    return load_runtime_config().pg_url


@pytest.fixture
def probe_table() -> Iterator[str]:
    pg_url = _get_test_pg_url()
    table_name = "doctor_composition_probe"

    async def _init_table() -> None:
        con = await asyncpg.connect(pg_url)
        try:
            await con.execute(f"DROP TABLE IF EXISTS {table_name}")
            await con.execute(
                f"""
                CREATE TABLE {table_name} (
                    id text PRIMARY KEY,
                    ctx text NOT NULL,
                    file_path text NOT NULL,
                    project text NOT NULL,
                    kind text
                )
                """
            )
        finally:
            await con.close()

    asyncio.run(_init_table())
    yield table_name

    async def _cleanup() -> None:
        con = await asyncpg.connect(pg_url)
        try:
            await con.execute(f"DROP TABLE IF EXISTS {table_name}")
        finally:
            await con.close()

    asyncio.run(_cleanup())


def _insert_rows(
    table_name: str, rows: list[tuple[str, str, str, str, str | None]]
) -> None:
    pg_url = _get_test_pg_url()

    async def _insert() -> None:
        con = await asyncpg.connect(pg_url)
        try:
            await con.executemany(
                f"""
                INSERT INTO {table_name} (id, ctx, file_path, project, kind)
                VALUES ($1, $2, $3, $4, $5)
                """,  # noqa: S608
                rows,
            )
        finally:
            await con.close()

    asyncio.run(_insert())


def test_the_three_numbers_hold_with_both_generations_present(
    probe_table: str,
) -> None:
    _insert_rows(
        probe_table,
        [
            ("1", "knowledge", "/Users/s/vault/knowledge/a.md", "knowledge", None),
            ("2", "knowledge", "knowledge/a.md", "vault", "doc"),
            ("3", "knowledge", "/Users/s/dev/axon/src/x.py", "axon", None),
            ("4", "knowledge", "src/x.py", "axon", "code"),
        ],
    )
    result = check_index_composition(pg_url=_get_test_pg_url(), table=probe_table)
    assert "total=4" in result.detail
    assert "vault_share=50.0%" in result.detail


def test_a_new_vault_row_is_counted_by_its_repo_identity_not_a_path_prefix(
    probe_table: str,
) -> None:
    _insert_rows(
        probe_table,
        [
            ("1", "knowledge", "knowledge/a.md", "vault", "doc"),
        ],
    )
    result = check_index_composition(pg_url=_get_test_pg_url(), table=probe_table)
    assert "total=1" in result.detail
    assert "vault_share=100.0%" in result.detail


def test_a_legacy_only_table_keeps_its_old_numbers(
    probe_table: str,
) -> None:
    _insert_rows(
        probe_table,
        [
            ("1", "knowledge", "/Users/s/vault/knowledge/a.md", "knowledge", None),
            ("2", "knowledge", "/Users/s/dev/axon/src/x.py", "axon", None),
        ],
    )
    result = check_index_composition(pg_url=_get_test_pg_url(), table=probe_table)
    assert "total=2" in result.detail
    assert "vault_share=50.0%" in result.detail


def test_career_and_plan_counts_are_unchanged(
    probe_table: str,
) -> None:
    _insert_rows(
        probe_table,
        [
            ("1", "career", "src/career.md", "career", "doc"),
            ("2", "knowledge", "docs/superpowers/plans/p.md", "axon", "spec"),
        ],
    )
    result = check_index_composition(pg_url=_get_test_pg_url(), table=probe_table)
    assert "career=1" in result.detail
    assert "plans=1" in result.detail


def test_a_plans_directory_at_the_repo_root_is_counted(
    probe_table: str,
) -> None:
    _insert_rows(
        probe_table,
        [
            ("1", "knowledge", "plans/p.md", "vault", "spec"),
            ("2", "knowledge", "/Users/s/vault/plans/p.md", "plans", None),
            ("3", "knowledge", "src/plans.py", "axon", "code"),
        ],
    )
    result = check_index_composition(pg_url=_get_test_pg_url(), table=probe_table)
    assert "plans=2" in result.detail
