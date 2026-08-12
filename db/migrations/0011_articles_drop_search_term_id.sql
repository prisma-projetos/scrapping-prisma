-- Última etapa: só remove articles.search_term_id depois de confirmar que
-- toda relação que ele representava já foi migrada para
-- article_search_matches (0008). Se sobrar alguma, aborta a migration em
-- vez de derrubar a coluna e perder informação silenciosamente.

DO $$
DECLARE
    unmigrated_count INTEGER;
BEGIN
    SELECT count(*) INTO unmigrated_count
    FROM articles a
    WHERE a.search_term_id IS NOT NULL
      AND NOT EXISTS (
          SELECT 1 FROM article_search_matches m
          WHERE m.article_id = a.id AND m.search_term_id = a.search_term_id
      );

    IF unmigrated_count > 0 THEN
        RAISE EXCEPTION
            'Migração abortada: % artigo(s) com search_term_id sem relação correspondente em article_search_matches',
            unmigrated_count;
    END IF;
END $$;

ALTER TABLE articles DROP COLUMN search_term_id;
