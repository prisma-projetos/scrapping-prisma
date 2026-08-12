-- Formaliza o vocabulário de extraction_status com um CHECK constraint.
-- 8 valores: os 6 do novo fluxo (pending/processing/success/failed/
-- blocked/empty) + 2 que já existem em produção com lógica própria
-- (paywalled, skipped_robots) — decisão confirmada para não perder
-- granularidade que o scraper já usa hoje. Os 5 valores em uso nas 204
-- linhas atuais (success, paywalled, blocked, failed, skipped_robots)
-- são subconjunto, então o ALTER é seguro.

ALTER TABLE articles ALTER COLUMN extraction_status SET DEFAULT 'pending';

ALTER TABLE articles ADD CONSTRAINT articles_extraction_status_check
    CHECK (
        extraction_status IN (
            'pending',
            'processing',
            'success',
            'failed',
            'blocked',
            'empty',
            'paywalled',
            'skipped_robots'
        )
    );
