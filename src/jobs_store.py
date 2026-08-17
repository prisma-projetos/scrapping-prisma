from __future__ import annotations

from dataclasses import dataclass

import psycopg

DEFAULT_PIPELINE_VERSION = "v1"


@dataclass(frozen=True)
class AnalysisJobRef:
    id: int
    article_id: int
    pipeline_version: str


def claim_next_job(conn: psycopg.Connection, *, pipeline_version: str = DEFAULT_PIPELINE_VERSION) -> AnalysisJobRef | None:
    """Reserva o próximo trabalho pendente de forma seura para múltiplos
    workers concorrentes: `FOR UPDATE SKIP LOCKED` faz cada worker pegar
    uma linha diferente em vez de travar esperando a mesma. Transação
    curta — comita antes de devolver, para não segurar lock durante o
    trabalho externo (chamada de IA), que é lento."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, article_id, pipeline_version
            FROM analysis_jobs
            WHERE status = 'pending' AND pipeline_version = %s
            ORDER BY scheduled_at
            FOR UPDATE SKIP LOCKED
            LIMIT 1
            """,
            (pipeline_version,),
        )
        row = cur.fetchone()
        if row is None:
            conn.commit()
            return None

        job_id, article_id, version = row
        cur.execute(
            """
            UPDATE analysis_jobs
            SET status = 'processing', attempts = attempts + 1, started_at = now()
            WHERE id = %s
            """,
            (job_id,),
        )
    conn.commit()
    return AnalysisJobRef(id=job_id, article_id=article_id, pipeline_version=version)


def mark_published(conn: psycopg.Connection, job_id: int) -> None:
    """Marca o job como publicado com sucesso no RabbitMQ (fronteira
    Scraper->Classifier, ver context.md raiz seção 7.3). O status gravado é
    'published_job' (não 'published') para não ficar ambíguo com
    `article.published_at`, que é a data em que a notícia foi publicada
    pelo veículo original -- conceito diferente do status deste job."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE analysis_jobs SET status = 'published_job', completed_at = now() WHERE id = %s",
            (job_id,),
        )
    conn.commit()


def fail_job(conn: psycopg.Connection, job_id: int, *, error: str, retry: bool = True) -> None:
    """Se `retry` e ainda houver tentativas disponíveis, volta para
    'pending' (uma próxima rodada do worker tenta de novo); senão marca
    'failed' definitivamente."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE analysis_jobs
            SET status = CASE
                    WHEN %(retry)s AND attempts < max_attempts THEN 'pending'
                    ELSE 'failed'
                END,
                error_message = %(error)s
            WHERE id = %(job_id)s
            """,
            {"retry": retry, "error": error, "job_id": job_id},
        )
    conn.commit()
