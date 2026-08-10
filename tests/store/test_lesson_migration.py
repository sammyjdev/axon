import re
from pathlib import Path


MIGRATION = Path(__file__).parents[2] / "src/axon/store/migrations/pg/0006_lessons_table.sql"


def test_lessons_migration_defines_cosine_hnsw_embedding() -> None:
    assert MIGRATION.exists()

    sql = MIGRATION.read_text(encoding="utf-8")
    assert re.search(r"\bembedding\s+vector\(1024\)", sql)
    assert "USING hnsw (embedding vector_cosine_ops)" in sql
