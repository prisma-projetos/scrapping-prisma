-- Adiciona 'published_job' ao vocabulário de status de analysis_jobs.
--
-- Contexto: a fronteira Scraper -> Classifier passou a usar RabbitMQ como
-- broker, com analysis_jobs funcionando como outbox transacional interno
-- ao scraper (ver context.md raiz, seção 7.3, e classifier-prisma/context.md,
-- seção 3). Um publisher novo (src/jobs_publisher.py) reaproveita
-- claim_next_job/fail_job já existentes e, ao publicar um job com sucesso
-- no RabbitMQ, precisa marcar esse job com um status dedicado.
--
-- O valor é 'published_job' (não 'published') para não ficar ambíguo com
-- `article.published_at`, que é a data em que a notícia foi publicada pelo
-- veículo original -- conceito diferente do status deste job.
--
-- Não inclui 'completed': esse status pertencia a um desenho anterior (o
-- Classifier consumiria analysis_jobs direto do Postgres) que foi
-- abandonado em favor do RabbitMQ, e nunca chegou a ser usado no fluxo
-- real -- ver `complete_job`, removida de src/jobs_store.py.
--
-- O CHECK constraint de analysis_jobs foi criado sem nome explícito dentro
-- do CREATE TABLE em 0006_analysis_jobs_schema.sql, então o Postgres deu a
-- ele o nome padrão <tabela>_<coluna>_check.

ALTER TABLE analysis_jobs DROP CONSTRAINT IF EXISTS analysis_jobs_status_check;

ALTER TABLE analysis_jobs ADD CONSTRAINT analysis_jobs_status_check
    CHECK (
        status IN (
            'pending',
            'processing',
            'published_job',
            'failed',
            'cancelled'
        )
    );
