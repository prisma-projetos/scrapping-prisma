-- Renomeia guid -> google_guid (mesmo dado, novo nome/semântica: pode ser
-- nulo quando o item de origem não tiver guid) e adiciona as colunas de
-- identidade/dedup em camadas usadas pelo find-or-create novo.

ALTER TABLE articles RENAME COLUMN guid TO google_guid;
ALTER TABLE articles ALTER COLUMN google_guid DROP NOT NULL;
ALTER TABLE articles DROP CONSTRAINT IF EXISTS articles_guid_key;

ALTER TABLE articles
    ADD COLUMN IF NOT EXISTS canonical_link TEXT,
    ADD COLUMN IF NOT EXISTS html_canonical_link TEXT,
    ADD COLUMN IF NOT EXISTS author TEXT,
    ADD COLUMN IF NOT EXISTS content_hash TEXT,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now();

CREATE TRIGGER trg_articles_updated_at
    BEFORE UPDATE ON articles
    FOR EACH ROW
    EXECUTE FUNCTION set_updated_at();
