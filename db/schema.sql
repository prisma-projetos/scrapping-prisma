-- HISTÓRICO — não é mais aplicado em runtime. Este arquivo era executado
-- inteiro a cada execução do script antes da introdução do sistema de
-- migrations (db/migrations/). Sua cópia congelada é 0001_baseline.sql;
-- o schema atual é o resultado de aplicar 0001..N em sequência.
-- Mantido só como referência do estado inicial do projeto.

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
