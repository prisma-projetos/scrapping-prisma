-- Marco zero: cópia exata do schema já efetivamente aplicado em produção
-- local (via db/schema.sql, reaplicado a cada execução com CREATE TABLE
-- IF NOT EXISTS). A partir daqui, alterações passam a ser migrations
-- numeradas e rastreadas em schema_migrations.

CREATE TABLE IF NOT EXISTS articles (
    id SERIAL PRIMARY KEY,
    guid TEXT NOT NULL UNIQUE,
    search_term_id TEXT NOT NULL,
    title TEXT NOT NULL,
    google_link TEXT NOT NULL,
    resolved_link TEXT,
    source_name TEXT,
    published_at TIMESTAMPTZ,
    collected_at TIMESTAMPTZ NOT NULL,
    full_text TEXT,
    extraction_status TEXT NOT NULL,
    extraction_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_articles_status ON articles(extraction_status);
