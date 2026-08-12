-- O trigger de enfileiramento (0006) só dispara em transições de status —
-- um artigo que já era 'success' ANTES do trigger existir nunca muda de
-- status de novo (não é reprocessado), então nunca dispararia o trigger e
-- ficaria de fora da fila para sempre. Este backfill garante que todo
-- artigo já bem-sucedido também entre na fila, uma vez, na criação dela.
INSERT INTO analysis_jobs (article_id, pipeline_version)
SELECT id, 'v1'
FROM articles
WHERE extraction_status = 'success'
ON CONFLICT (article_id, pipeline_version) DO NOTHING;
