-- Fila de trabalhos para o futuro pipeline de IA. Nenhum modelo é chamado
-- aqui — só a fila e o gatilho que a popula automaticamente.

CREATE TABLE IF NOT EXISTS analysis_jobs (
    id BIGSERIAL PRIMARY KEY,

    article_id BIGINT NOT NULL
        REFERENCES articles(id)
        ON DELETE CASCADE,

    pipeline_version TEXT NOT NULL DEFAULT 'v1',

    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (
            status IN (
                'pending',
                'processing',
                'completed',
                'failed',
                'cancelled'
            )
        ),

    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,

    scheduled_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,

    error_message TEXT,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (article_id, pipeline_version)
);

CREATE TRIGGER trg_analysis_jobs_updated_at
    BEFORE UPDATE ON analysis_jobs
    FOR EACH ROW
    EXECUTE FUNCTION set_updated_at();

-- Índice parcial: só interessa localizar rapidamente trabalhos pendentes,
-- ordenados por agendamento.
CREATE INDEX IF NOT EXISTS idx_analysis_jobs_pending_scheduled
    ON analysis_jobs(scheduled_at)
    WHERE status = 'pending';

CREATE OR REPLACE FUNCTION enqueue_article_analysis()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.extraction_status = 'success'
       AND (
           TG_OP = 'INSERT'
           OR OLD.extraction_status IS DISTINCT FROM NEW.extraction_status
       )
    THEN
        INSERT INTO analysis_jobs (
            article_id,
            pipeline_version
        )
        VALUES (
            NEW.id,
            'v1'
        )
        ON CONFLICT (article_id, pipeline_version)
        DO NOTHING;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Escopado só a mudanças em extraction_status: um UPDATE que não toca essa
-- coluna (ex.: backfill de canonical_link/content_hash em migrations
-- futuras) não dispara o trigger, então não reenfileira artigos que já
-- eram 'success' antes da migration.
CREATE TRIGGER trg_enqueue_article_analysis
    AFTER INSERT OR UPDATE OF extraction_status ON articles
    FOR EACH ROW
    EXECUTE FUNCTION enqueue_article_analysis();
