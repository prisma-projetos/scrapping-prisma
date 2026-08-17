from __future__ import annotations

from datetime import datetime, timezone

import articles_store
import jobs_publisher
import jobs_store
from rabbitmq_client import PublishError


class FakePublisher:
    """Substitui `RabbitMQPublisher` nos testes -- sem broker real. Grava
    cada payload publicado; se `should_fail` for True, levanta
    `PublishError` (simulando broker indisponível/erro de conexão) em vez
    de publicar."""

    def __init__(self, should_fail: bool = False):
        self.should_fail = should_fail
        self.published: list[dict] = []
        self.calls = 0

    def publish(self, payload: dict) -> None:
        self.calls += 1
        if self.should_fail:
            raise PublishError("broker indisponível (simulado)")
        self.published.append(payload)


def _create_article_with_full_text(
    conn,
    *,
    guid="guid-1",
    canonical="https://example.com/a",
    title="Título do artigo",
    source_name="Veículo Exemplo",
    published_at=None,
):
    published_at = published_at or datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)
    ref = articles_store.find_or_create_article(
        conn,
        google_guid=guid,
        canonical_link=canonical,
        title=title,
        google_link="g",
        resolved_link=canonical,
        source_name=source_name,
        published_at=published_at,
        collected_at=datetime.now(timezone.utc),
    )
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE articles SET full_text = %s, extraction_status = 'success' WHERE id = %s",
            ("Texto completo do artigo, grande o suficiente para o teste.", ref.id),
        )
    conn.commit()
    return ref


def _job_status(conn, article_id):
    with conn.cursor() as cur:
        cur.execute("SELECT status, attempts FROM analysis_jobs WHERE article_id = %s", (article_id,))
        return cur.fetchone()


def test_publish_pending_jobs_marks_published_and_stops_returning_job(db_conn):
    ref = _create_article_with_full_text(db_conn)
    publisher = FakePublisher()

    result = jobs_publisher.publish_pending_jobs(db_conn, publisher)

    assert result == {"published": 1, "failed": 0}
    status, _ = _job_status(db_conn, ref.id)
    assert status == "published_job"

    # o job publicado não é mais 'pending' -- claim_next_job não deve devolvê-lo
    assert jobs_store.claim_next_job(db_conn) is None


def test_publish_failure_triggers_fail_job(db_conn):
    ref = _create_article_with_full_text(db_conn)
    with db_conn.cursor() as cur:
        cur.execute("UPDATE analysis_jobs SET max_attempts = 1 WHERE article_id = %s", (ref.id,))
    db_conn.commit()

    publisher = FakePublisher(should_fail=True)
    result = jobs_publisher.publish_pending_jobs(db_conn, publisher)

    assert result == {"published": 0, "failed": 1}
    with db_conn.cursor() as cur:
        cur.execute("SELECT status, error_message FROM analysis_jobs WHERE article_id = %s", (ref.id,))
        status, error_message = cur.fetchone()
    assert status == "failed"
    assert "indisponível" in error_message
    assert publisher.published == []


def test_publish_pending_jobs_stops_after_consecutive_failures(db_conn):
    # Mais jobs pendentes do que o limite de falhas consecutivas -- simula
    # o RabbitMQ fora do ar durante toda a execução. O publisher não deve
    # tentar drenar a fila inteira contra um broker indisponível.
    #
    # max_attempts=1 em cada job para que uma falha de publicação marque o
    # job como 'failed' de uma vez (sem `fail_job` devolvê-lo para
    # 'pending'), garantindo que cada iteração do loop reivindique um job
    # DIFERENTE -- senão claim_next_job (ORDER BY scheduled_at) ficaria
    # reclamando o mesmo job repetidas vezes até ele esgotar as tentativas,
    # o que testaria retry, não o circuit breaker entre jobs distintos.
    total_jobs = jobs_publisher.MAX_CONSECUTIVE_FAILURES + 2
    for i in range(total_jobs):
        ref = _create_article_with_full_text(db_conn, guid=f"guid-cb-{i}", canonical=f"https://example.com/cb-{i}")
        with db_conn.cursor() as cur:
            cur.execute("UPDATE analysis_jobs SET max_attempts = 1 WHERE article_id = %s", (ref.id,))
        db_conn.commit()

    publisher = FakePublisher(should_fail=True)
    result = jobs_publisher.publish_pending_jobs(db_conn, publisher)

    assert publisher.calls == jobs_publisher.MAX_CONSECUTIVE_FAILURES
    assert result == {"published": 0, "failed": jobs_publisher.MAX_CONSECUTIVE_FAILURES}

    # jobs além do limite nunca chegaram a ser reivindicados -- continuam
    # 'pending', disponíveis para a próxima execução (ex.: próximo cron)
    with db_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM analysis_jobs WHERE status = 'pending'")
        pending_count = cur.fetchone()[0]
    assert pending_count == total_jobs - jobs_publisher.MAX_CONSECUTIVE_FAILURES


def test_publish_pending_jobs_is_idempotent_across_runs(db_conn):
    ref = _create_article_with_full_text(db_conn)
    publisher = FakePublisher()

    first = jobs_publisher.publish_pending_jobs(db_conn, publisher)
    second = jobs_publisher.publish_pending_jobs(db_conn, publisher)

    assert first == {"published": 1, "failed": 0}
    assert second == {"published": 0, "failed": 0}
    assert len(publisher.published) == 1  # não publicou o mesmo job duas vezes


def test_published_payload_contains_expected_fields(db_conn):
    published_at = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)
    ref = _create_article_with_full_text(
        db_conn,
        title="Político anuncia proposta",
        source_name="Jornal Exemplo",
        canonical="https://example.com/noticia",
        published_at=published_at,
    )
    publisher = FakePublisher()

    jobs_publisher.publish_pending_jobs(db_conn, publisher)

    assert len(publisher.published) == 1
    payload = publisher.published[0]
    assert payload["schema_version"] == 1
    assert payload["article_id"] == ref.id
    assert isinstance(payload["job_id"], int)
    assert payload["pipeline_version"] == "v1"
    assert payload["title"] == "Político anuncia proposta"
    assert payload["full_text"] == "Texto completo do artigo, grande o suficiente para o teste."
    assert payload["canonical_link"] == "https://example.com/noticia"
    assert payload["source_name"] == "Jornal Exemplo"
    assert payload["published_at"] == published_at.isoformat()
