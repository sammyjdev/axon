"""The lessons table is created by its store, not by the session migration chain.

The first attempt put it in `migrations/pg/0006_lessons_table.sql`. That chain belongs to the
SESSION store, it runs against databases where the `vector` extension was never installed,
and no migration in it creates an extension - that bootstrap lives in
`pg_vector_store._ensure_pool`, which runs `CREATE EXTENSION IF NOT EXISTS vector` and then
the dimension guard. Adding a vector column there broke 18 tests with
`type "vector" does not exist`, and would have forced pgvector into every session database.

The gate caught it on the merge, which is the only reason it is not in the tree. The spec
named the wrong anchor: "migrations: numbered SQL in migrations/pg/" is true of the session
store and false of anything holding a vector.
"""
import pathlib

MIGRATIONS = pathlib.Path(__file__).resolve().parents[2] / "src/axon/store/migrations/pg"


def test_no_vector_column_enters_the_session_migration_chain():
    for sql in MIGRATIONS.glob("*.sql"):
        body = sql.read_text().lower()
        assert "vector(" not in body, (
            f"{sql.name} declares a vector column. That chain runs against databases with no "
            f"pgvector extension; vector tables are created by their store, which bootstraps "
            f"the extension first.")
