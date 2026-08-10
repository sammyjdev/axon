-- 0006_lessons_table: durable lesson records with cosine vector retrieval.

CREATE TABLE IF NOT EXISTS lessons (
    id          uuid PRIMARY KEY,
    kind        text NOT NULL,
    triggers    text[] NOT NULL,
    mistake     text NOT NULL,
    tell        text NOT NULL,
    fix         text NOT NULL,
    source      text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    embedding   vector(1024)
);

CREATE INDEX IF NOT EXISTS idx_lessons_embedding_hnsw
    ON lessons USING hnsw (embedding vector_cosine_ops);
