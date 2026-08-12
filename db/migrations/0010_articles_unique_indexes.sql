-- Índices únicos parciais (só valores não nulos) para google_guid e
-- canonical_link — as duas evidências fortes usadas para dedup na
-- criação de um artigo. Roda depois do backfill (0009), senão colisões
-- em dados legados fariam esta migration falhar de forma menos clara.
CREATE UNIQUE INDEX IF NOT EXISTS idx_articles_google_guid_unique
    ON articles(google_guid) WHERE google_guid IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_articles_canonical_link_unique
    ON articles(canonical_link) WHERE canonical_link IS NOT NULL;

-- Índices simples pedidos para consulta/filtragem. Não criamos mais um
-- índice simples de canonical_link à parte: o índice único parcial acima
-- já serve para busca por igualdade em valores não nulos, duplicá-lo
-- só adicionaria manutenção sem ganho real.
CREATE INDEX IF NOT EXISTS idx_articles_published_at ON articles(published_at);
CREATE INDEX IF NOT EXISTS idx_articles_source_name ON articles(source_name);
CREATE INDEX IF NOT EXISTS idx_articles_content_hash ON articles(content_hash);
