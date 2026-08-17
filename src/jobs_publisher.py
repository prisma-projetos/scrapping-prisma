from __future__ import annotations

import logging
from typing import Protocol

import articles_store
import jobs_store
from rabbitmq_client import PublishError

# Campo de versionamento do payload -- permite ao Classifier lidar com
# mudanças de contrato no futuro sem quebrar consumidores antigos (ver
# README, seção "Publisher"). Bump manual sempre que o formato do payload
# mudar de forma incompatível.
SCHEMA_VERSION = 1

# Circuit breaker simples: N falhas de publicação SEGUIDAS (não falhas
# espalhadas entre jobs saudáveis) são um sinal muito mais forte de "o
# RabbitMQ está fora do ar" do que de "esse job específico tem problema".
# Sem isso, uma fila com centenas de jobs pendentes bateria centenas de
# vezes contra um broker indisponível numa única execução, sem necessidade
# -- é melhor parar cedo, deixar os jobs restantes 'pending' e tentar de
# novo na próxima execução agendada (ver README, seção "Agendamento").
MAX_CONSECUTIVE_FAILURES = 3

logger = logging.getLogger("politica")


class Publisher(Protocol):
    """Qualquer objeto com `.publish(dict)` que levanta `PublishError` em
    caso de falha -- normalmente um `RabbitMQPublisher`, mas os testes
    passam um fake/mock sem precisar de um broker real."""

    def publish(self, payload: dict) -> None: ...


def build_payload(job: jobs_store.AnalysisJobRef, article: articles_store.ArticleForPublishing) -> dict:
    """Monta a mensagem publicada no RabbitMQ. Inclui o conteúdo do artigo
    (não só o `article_id`) para o Classifier nunca precisar voltar a este
    Postgres -- ver context.md raiz, seção 7.3."""
    return {
        "schema_version": SCHEMA_VERSION,
        "job_id": job.id,
        "article_id": article.id,
        "pipeline_version": job.pipeline_version,
        "title": article.title,
        "full_text": article.full_text,
        "canonical_link": article.canonical_link,
        "source_name": article.source_name,
        "published_at": article.published_at.isoformat() if article.published_at else None,
    }


def publish_pending_jobs(
    conn,
    publisher: Publisher,
    *,
    pipeline_version: str = jobs_store.DEFAULT_PIPELINE_VERSION,
) -> dict[str, int]:
    """Publica todos os jobs pendentes disponíveis no momento da chamada,
    um de cada vez, e sai -- mesmo padrão de execução única de
    `src/main.py` (agendável via cron, sem processo daemon). Reaproveita
    `claim_next_job`/`fail_job` de `jobs_store.py`, sem duplicar a lógica
    de claim nem o retry por tentativas.

    Cada iteração é isolada: se a publicação de um job falhar, os demais
    continuam sendo tentados -- a menos que as falhas de publicação (não as
    de "artigo não encontrado", que são um problema do job, não do broker)
    se acumulem em sequência sem nenhum sucesso no meio, aí a execução para
    cedo (ver `MAX_CONSECUTIVE_FAILURES`). Retorna a contagem de
    publicados/falhos desta execução (parcial, se parou cedo)."""
    published = 0
    failed = 0
    consecutive_failures = 0

    while True:
        job = jobs_store.claim_next_job(conn, pipeline_version=pipeline_version)
        if job is None:
            break

        article = articles_store.find_for_publishing(conn, job.article_id)
        if article is None:
            # Não deveria acontecer (FK com ON DELETE CASCADE), mas não
            # deixa o job preso em 'processing' para sempre se acontecer.
            # Problema é do job, não do broker -- não conta para o circuit
            # breaker.
            logger.error("Job %s referencia artigo %s inexistente -- marcando como falho", job.id, job.article_id)
            jobs_store.fail_job(conn, job.id, error="Artigo não encontrado para publicação", retry=False)
            failed += 1
            continue

        payload = build_payload(job, article)
        try:
            publisher.publish(payload)
        except PublishError as exc:
            logger.exception("Falha ao publicar job %s (artigo %s) no RabbitMQ", job.id, job.article_id)
            jobs_store.fail_job(conn, job.id, error=str(exc))
            failed += 1
            consecutive_failures += 1
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                logger.error(
                    "Interrompendo execução: %d falhas consecutivas ao publicar -- "
                    "RabbitMQ provavelmente indisponível. Jobs restantes ficam "
                    "'pending' para a próxima execução agendada.",
                    consecutive_failures,
                )
                return {"published": published, "failed": failed}
            continue

        jobs_store.mark_published(conn, job.id)
        published += 1
        consecutive_failures = 0
        logger.info("Job %s (artigo %s) publicado no RabbitMQ", job.id, job.article_id)

    return {"published": published, "failed": failed}
