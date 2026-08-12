-- Tabela própria para termos de busca (hoje só existem soltos no YAML,
-- sem rastreabilidade nem relação formal com os artigos coletados).

CREATE TABLE IF NOT EXISTS search_terms (
    id TEXT PRIMARY KEY,
    query TEXT NOT NULL,
    category TEXT NOT NULL,
    entity TEXT,
    topic TEXT,

    retrieval_type TEXT NOT NULL DEFAULT 'neutral'
        CHECK (
            retrieval_type IN (
                'neutral',
                'opinion_query',
                'entity_topic',
                'event'
            )
        ),

    enabled BOOLEAN NOT NULL DEFAULT TRUE,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_search_terms_enabled ON search_terms(enabled);
CREATE INDEX IF NOT EXISTS idx_search_terms_entity ON search_terms(entity);
CREATE INDEX IF NOT EXISTS idx_search_terms_topic ON search_terms(topic);

-- Função genérica de updated_at, reutilizada por outras tabelas que
-- ganham essa coluna nas próximas migrations (articles, analysis_jobs).
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_search_terms_updated_at
    BEFORE UPDATE ON search_terms
    FOR EACH ROW
    EXECUTE FUNCTION set_updated_at();
