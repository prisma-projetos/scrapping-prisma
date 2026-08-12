from __future__ import annotations

from datetime import datetime, timezone

import articles_store
import jobs_store


def _create_article(conn, guid="guid-1", canonical="https://example.com/a"):
    return articles_store.find_or_create_article(
        conn,
        google_guid=guid,
        canonical_link=canonical,
        title="Título",
        google_link="g",
        resolved_link=canonical,
        source_name=None,
        published_at=None,
        collected_at=datetime.now(timezone.utc),
    )


def _set_status(conn, article_id, status):
    with conn.cursor() as cur:
        cur.execute("UPDATE articles SET extraction_status = %s WHERE id = %s", (status, article_id))
    conn.commit()


def test_article_success_enqueues_analysis_job(db_conn):
    ref = _create_article(db_conn)
    _set_status(db_conn, ref.id, "success")

    with db_conn.cursor() as cur:
        cur.execute("SELECT status, pipeline_version FROM analysis_jobs WHERE article_id = %s", (ref.id,))
        rows = cur.fetchall()

    assert len(rows) == 1
    assert rows[0] == ("pending", "v1")


def test_failed_blocked_empty_do_not_enqueue_job(db_conn):
    for status in ("failed", "blocked", "empty"):
        ref = _create_article(db_conn, guid=f"guid-{status}", canonical=f"https://example.com/{status}")
        _set_status(db_conn, ref.id, status)

        with db_conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM analysis_jobs WHERE article_id = %s", (ref.id,))
            assert cur.fetchone()[0] == 0


def test_job_not_duplicated_when_status_oscillates_back_to_success(db_conn):
    ref = _create_article(db_conn)
    _set_status(db_conn, ref.id, "success")
    _set_status(db_conn, ref.id, "failed")
    _set_status(db_conn, ref.id, "success")

    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM analysis_jobs WHERE article_id = %s AND pipeline_version = 'v1'",
            (ref.id,),
        )
        assert cur.fetchone()[0] == 1


def test_claim_next_job_marks_processing_and_increments_attempts(db_conn):
    ref = _create_article(db_conn)
    _set_status(db_conn, ref.id, "success")

    claimed = jobs_store.claim_next_job(db_conn)
    assert claimed is not None
    assert claimed.article_id == ref.id

    with db_conn.cursor() as cur:
        cur.execute("SELECT status, attempts FROM analysis_jobs WHERE id = %s", (claimed.id,))
        status, attempts = cur.fetchone()
        assert status == "processing"
        assert attempts == 1

    # já foi claimado — não deve devolver o mesmo job de novo
    second_claim = jobs_store.claim_next_job(db_conn)
    assert second_claim is None


def test_complete_job(db_conn):
    ref = _create_article(db_conn)
    _set_status(db_conn, ref.id, "success")
    claimed = jobs_store.claim_next_job(db_conn)

    jobs_store.complete_job(db_conn, claimed.id)

    with db_conn.cursor() as cur:
        cur.execute("SELECT status, completed_at FROM analysis_jobs WHERE id = %s", (claimed.id,))
        status, completed_at = cur.fetchone()
        assert status == "completed"
        assert completed_at is not None


def test_fail_job_marks_failed_when_attempts_exhausted(db_conn):
    ref = _create_article(db_conn)
    _set_status(db_conn, ref.id, "success")

    with db_conn.cursor() as cur:
        cur.execute("SELECT id FROM analysis_jobs WHERE article_id = %s", (ref.id,))
        job_id = cur.fetchone()[0]
        cur.execute("UPDATE analysis_jobs SET max_attempts = 1 WHERE id = %s", (job_id,))
    db_conn.commit()

    jobs_store.claim_next_job(db_conn)  # attempts vira 1
    jobs_store.fail_job(db_conn, job_id, error="erro simulado", retry=True)

    with db_conn.cursor() as cur:
        cur.execute("SELECT status, error_message FROM analysis_jobs WHERE id = %s", (job_id,))
        status, error_message = cur.fetchone()
        assert status == "failed"  # attempts (1) já não é < max_attempts (1)
        assert error_message == "erro simulado"
