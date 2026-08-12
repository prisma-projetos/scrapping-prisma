-- Relação M:N entre articles e search_terms, com snapshot dos campos do
-- termo no momento da coleta (preservado mesmo que o termo mude depois).

CREATE TABLE IF NOT EXISTS article_search_matches (
    id BIGSERIAL PRIMARY KEY,

    article_id BIGINT NOT NULL
        REFERENCES articles(id)
        ON DELETE CASCADE,

    search_term_id TEXT NOT NULL
        REFERENCES search_terms(id)
        ON DELETE RESTRICT,

    query_snapshot TEXT NOT NULL,
    category_snapshot TEXT,
    entity_snapshot TEXT,
    topic_snapshot TEXT,

    retrieval_type_snapshot TEXT NOT NULL DEFAULT 'neutral'
        CHECK (
            retrieval_type_snapshot IN (
                'neutral',
                'opinion_query',
                'entity_topic',
                'event'
            )
        ),

    matched_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (article_id, search_term_id)
);

CREATE INDEX IF NOT EXISTS idx_article_search_matches_article_id ON article_search_matches(article_id);
-- A UNIQUE composta (article_id, search_term_id) já cria um índice, mas ele
-- não serve para localizar todas as ocorrências de um termo isolado (o
-- article_id não é o primeiro campo do índice em toda consulta), daí este
-- índice adicional.
CREATE INDEX IF NOT EXISTS idx_article_search_matches_search_term_id ON article_search_matches(search_term_id);
CREATE INDEX IF NOT EXISTS idx_article_search_matches_entity_snapshot ON article_search_matches(entity_snapshot);
CREATE INDEX IF NOT EXISTS idx_article_search_matches_topic_snapshot ON article_search_matches(topic_snapshot);
