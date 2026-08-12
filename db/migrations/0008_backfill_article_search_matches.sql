-- Migra as relações artigo<->termo que hoje só existem como
-- articles.search_term_id para a tabela M:N nova. Roda depois de 0007
-- (search_terms já populado), senão a FK RESTRICT de search_term_id
-- derrubaria o INSERT.
--
-- query_snapshot/category_snapshot/entity_snapshot/topic_snapshot ficam
-- claramente marcados como migração: o schema antigo não guardava a
-- consulta nem a categorização usada no momento da coleta original, então
-- não inventamos esses valores a partir do search_terms.yaml atual (que
-- pode ter uma query totalmente diferente da usada quando o artigo foi
-- coletado de fato).

INSERT INTO article_search_matches (
    article_id,
    search_term_id,
    query_snapshot,
    category_snapshot,
    entity_snapshot,
    topic_snapshot,
    retrieval_type_snapshot,
    matched_at
)
SELECT
    a.id,
    a.search_term_id,
    '[MIGRAÇÃO: consulta original não registrada pelo schema anterior]',
    NULL,
    NULL,
    NULL,
    'neutral',
    a.collected_at
FROM articles a
WHERE a.search_term_id IS NOT NULL
ON CONFLICT (article_id, search_term_id) DO NOTHING;
