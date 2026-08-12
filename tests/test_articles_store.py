from __future__ import annotations

import os
import threading
from datetime import datetime, timezone

import articles_store
from conftest import connect_to


def _now():
    return datetime.now(timezone.utc)


def _insert_search_term(conn, term_id="termo-teste", query="consulta teste", category="politician"):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO search_terms (id, query, category) VALUES (%s, %s, %s) ON CONFLICT (id) DO NOTHING",
            (term_id, query, category),
        )
    conn.commit()


def test_find_or_create_article_inserts_new(db_conn):
    ref = articles_store.find_or_create_article(
        db_conn,
        google_guid="guid-1",
        canonical_link="https://example.com/a",
        title="Título A",
        google_link="https://news.google.com/rss/articles/xxx",
        resolved_link="https://example.com/a",
        source_name="Fonte X",
        published_at=None,
        collected_at=_now(),
    )
    assert ref.is_new is True
    assert ref.extraction_status == "pending"


def test_find_or_create_article_same_guid_does_not_duplicate(db_conn):
    kwargs = dict(
        title="Título A",
        google_link="https://news.google.com/rss/articles/xxx",
        resolved_link="https://example.com/a",
        source_name="Fonte X",
        published_at=None,
        collected_at=_now(),
    )
    first = articles_store.find_or_create_article(
        db_conn, google_guid="guid-1", canonical_link="https://example.com/a", **kwargs
    )
    second = articles_store.find_or_create_article(
        db_conn, google_guid="guid-1", canonical_link="https://example.com/a", **kwargs
    )

    assert first.id == second.id
    assert second.is_new is False

    with db_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM articles")
        assert cur.fetchone()[0] == 1


def test_find_or_create_article_recognizes_same_canonical_link_with_different_guid(db_conn):
    kwargs = dict(
        title="Título A",
        google_link="https://news.google.com/rss/articles/xxx",
        resolved_link="https://example.com/a",
        source_name="Fonte X",
        published_at=None,
        collected_at=_now(),
    )
    first = articles_store.find_or_create_article(
        db_conn, google_guid="guid-1", canonical_link="https://example.com/a", **kwargs
    )
    # mesmo artigo, mas o RSS de outra busca trouxe um guid diferente
    second = articles_store.find_or_create_article(
        db_conn, google_guid="guid-2-outro-termo", canonical_link="https://example.com/a", **kwargs
    )

    assert first.id == second.id
    assert second.is_new is False

    with db_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM articles")
        assert cur.fetchone()[0] == 1


def test_content_hash_is_deterministic_across_whitespace_variance():
    text_a = "Texto de exemplo.  Com   espaços   repetidos.\n\nE quebras de linha."
    text_b = "Texto de exemplo. Com espaços repetidos. E quebras de linha."
    assert articles_store.compute_content_hash(text_a) == articles_store.compute_content_hash(text_b)


def test_content_hash_differs_for_different_text():
    assert articles_store.compute_content_hash("texto 1") != articles_store.compute_content_hash("texto 2")


def test_article_found_by_two_different_terms_creates_two_matches_not_two_articles(db_conn):
    _insert_search_term(db_conn, "termo-1", "consulta 1", "politician")
    _insert_search_term(db_conn, "termo-2", "consulta 2", "topic")

    ref = articles_store.find_or_create_article(
        db_conn,
        google_guid="guid-1",
        canonical_link="https://example.com/a",
        title="Título A",
        google_link="https://news.google.com/rss/articles/xxx",
        resolved_link="https://example.com/a",
        source_name="Fonte X",
        published_at=None,
        collected_at=_now(),
    )

    is_new_1 = articles_store.upsert_match(
        db_conn,
        article_id=ref.id,
        search_term_id="termo-1",
        matched_query="consulta 1",
        matched_category="politician",
        matched_entity=None,
        matched_topic=None,
        matched_retrieval_type="neutral",
        collected_at=_now(),
    )
    is_new_2 = articles_store.upsert_match(
        db_conn,
        article_id=ref.id,
        search_term_id="termo-2",
        matched_query="consulta 2",
        matched_category="topic",
        matched_entity=None,
        matched_topic="economy",
        matched_retrieval_type="neutral",
        collected_at=_now(),
    )

    assert is_new_1 is True
    assert is_new_2 is True

    with db_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM articles")
        assert cur.fetchone()[0] == 1
        cur.execute("SELECT count(*) FROM article_search_matches WHERE article_id = %s", (ref.id,))
        assert cur.fetchone()[0] == 2


def test_upsert_match_does_not_duplicate_same_relation(db_conn):
    _insert_search_term(db_conn)
    ref = articles_store.find_or_create_article(
        db_conn,
        google_guid="guid-1",
        canonical_link="https://example.com/a",
        title="A",
        google_link="g",
        resolved_link="https://example.com/a",
        source_name=None,
        published_at=None,
        collected_at=_now(),
    )
    match_kwargs = dict(
        article_id=ref.id,
        search_term_id="termo-teste",
        matched_query="consulta teste",
        matched_category="politician",
        matched_entity=None,
        matched_topic=None,
        matched_retrieval_type="neutral",
        collected_at=_now(),
    )
    first = articles_store.upsert_match(db_conn, **match_kwargs)
    second = articles_store.upsert_match(db_conn, **match_kwargs)

    assert first is True
    assert second is False

    with db_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM article_search_matches WHERE article_id = %s", (ref.id,))
        assert cur.fetchone()[0] == 1


def test_match_snapshot_is_preserved_after_search_term_changes(db_conn):
    _insert_search_term(db_conn, "termo-teste", "consulta original", "politician")
    ref = articles_store.find_or_create_article(
        db_conn,
        google_guid="guid-1",
        canonical_link="https://example.com/a",
        title="A",
        google_link="g",
        resolved_link="https://example.com/a",
        source_name=None,
        published_at=None,
        collected_at=_now(),
    )
    articles_store.upsert_match(
        db_conn,
        article_id=ref.id,
        search_term_id="termo-teste",
        matched_query="consulta original",
        matched_category="politician",
        matched_entity=None,
        matched_topic=None,
        matched_retrieval_type="neutral",
        collected_at=_now(),
    )

    with db_conn.cursor() as cur:
        cur.execute("UPDATE search_terms SET query = 'consulta totalmente diferente' WHERE id = 'termo-teste'")
    db_conn.commit()

    with db_conn.cursor() as cur:
        cur.execute("SELECT query_snapshot FROM article_search_matches WHERE article_id = %s", (ref.id,))
        assert cur.fetchone()[0] == "consulta original"


def test_update_after_extraction_clears_error_on_success(db_conn):
    ref = articles_store.find_or_create_article(
        db_conn,
        google_guid="guid-1",
        canonical_link="https://example.com/a",
        title="A",
        google_link="g",
        resolved_link="https://example.com/a",
        source_name=None,
        published_at=None,
        collected_at=_now(),
    )
    articles_store.claim_for_extraction(db_conn, ref.id)
    articles_store.update_after_extraction(
        db_conn,
        ref.id,
        resolved_link="https://example.com/a",
        html_canonical_link=None,
        full_text=None,
        author=None,
        content_hash=None,
        extraction_status="failed",
        extraction_error="timeout",
    )

    with db_conn.cursor() as cur:
        cur.execute("SELECT extraction_status, extraction_error FROM articles WHERE id = %s", (ref.id,))
        status, error = cur.fetchone()
        assert status == "failed"
        assert error == "timeout"

    reclaimed = articles_store.claim_for_extraction(db_conn, ref.id)
    assert reclaimed is True

    full_text = "texto completo do artigo, longo o bastante para não ser tratado como vazio neste teste"
    articles_store.update_after_extraction(
        db_conn,
        ref.id,
        resolved_link="https://example.com/a",
        html_canonical_link=None,
        full_text=full_text,
        author=None,
        content_hash=articles_store.compute_content_hash(full_text),
        extraction_status="success",
        extraction_error=None,
    )

    with db_conn.cursor() as cur:
        cur.execute("SELECT extraction_status, extraction_error FROM articles WHERE id = %s", (ref.id,))
        status, error = cur.fetchone()
        assert status == "success"
        assert error is None


def test_claim_for_extraction_returns_false_once_article_is_success(db_conn):
    ref = articles_store.find_or_create_article(
        db_conn,
        google_guid="guid-1",
        canonical_link="https://example.com/a",
        title="A",
        google_link="g",
        resolved_link="https://example.com/a",
        source_name=None,
        published_at=None,
        collected_at=_now(),
    )
    articles_store.claim_for_extraction(db_conn, ref.id)
    articles_store.update_after_extraction(
        db_conn,
        ref.id,
        resolved_link="https://example.com/a",
        html_canonical_link=None,
        full_text="texto grande o suficiente para não ser considerado vazio neste teste também",
        author=None,
        content_hash="hash-qualquer",
        extraction_status="success",
        extraction_error=None,
    )

    claimed_again = articles_store.claim_for_extraction(db_conn, ref.id)
    assert claimed_again is False


def test_concurrent_find_or_create_same_article_results_in_single_row(db_conn):
    kwargs = dict(
        title="Concorrência",
        google_link="g",
        resolved_link="https://example.com/concorrencia",
        source_name=None,
        published_at=None,
        collected_at=_now(),
    )

    results: list[int] = []
    errors: list[Exception] = []

    def worker():
        conn = connect_to(os.environ["POSTGRES_TEST_DB"])
        try:
            ref = articles_store.find_or_create_article(
                conn,
                google_guid="guid-concorrencia",
                canonical_link="https://example.com/concorrencia",
                **kwargs,
            )
            results.append(ref.id)
        except Exception as exc:  # pragma: no cover - só dispara se o design de dedup falhar
            errors.append(exc)
        finally:
            conn.close()

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"exceções não tratadas na corrida: {errors}"
    assert len(set(results)) == 1

    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM articles WHERE canonical_link = %s",
            ("https://example.com/concorrencia",),
        )
        assert cur.fetchone()[0] == 1
