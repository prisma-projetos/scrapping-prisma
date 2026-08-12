from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from conftest import connect_to, create_database, drop_database
from storage import run_migrations


@pytest.fixture()
def temp_db():
    """Banco totalmente à parte do politica_test compartilhado — este
    teste precisa controlar em que ponto exato da sequência de migrations
    o banco está, então não pode reusar um banco já 100% migrado."""
    name = f"politica_test_migration_{uuid.uuid4().hex[:8]}"
    create_database(name)
    try:
        yield name
    finally:
        drop_database(name)


def test_migration_preserves_legacy_data_and_backfills_relations(temp_db):
    conn = connect_to(temp_db)
    try:
        # 1. Só a baseline — schema idêntico ao que está em produção hoje
        run_migrations(conn, up_to="0001_baseline")

        # 2. Semeia uma linha no formato legado (search_term_id direto, sem search_terms)
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO articles (
                    guid, search_term_id, title, google_link, resolved_link,
                    source_name, published_at, collected_at, full_text,
                    extraction_status, extraction_error
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    "guid-legado-1",
                    "termo-legado",
                    "Título legado",
                    "https://news.google.com/rss/articles/xxx",
                    "https://example.com/artigo-legado",
                    "Fonte Legada",
                    datetime.now(timezone.utc),
                    datetime.now(timezone.utc),
                    "Texto completo do artigo legado, grande o suficiente para não ser vazio.",
                    "success",
                    None,
                ),
            )
        conn.commit()

        # 3. Aplica o restante da sequência
        applied = run_migrations(conn)
        assert "0011_articles_drop_search_term_id" in applied

        # 4. A linha legada foi preservada (guid virou google_guid via rename)
        with conn.cursor() as cur:
            cur.execute("SELECT id, title, extraction_status FROM articles WHERE google_guid = 'guid-legado-1'")
            row = cur.fetchone()
        assert row is not None
        article_id, title, status = row
        assert title == "Título legado"
        assert status == "success"

        # 5. search_terms foi backfilled com o termo órfão (fallback defensivo da 0007,
        # já que "termo-legado" não existe no search_terms.yaml real do projeto)
        with conn.cursor() as cur:
            cur.execute("SELECT category, enabled FROM search_terms WHERE id = 'termo-legado'")
            term_row = cur.fetchone()
        assert term_row is not None
        assert term_row[0] == "legacy_migration"
        assert term_row[1] is False

        # 6. a relação artigo<->termo foi migrada para article_search_matches
        with conn.cursor() as cur:
            cur.execute(
                "SELECT search_term_id, query_snapshot FROM article_search_matches WHERE article_id = %s",
                (article_id,),
            )
            match_row = cur.fetchone()
        assert match_row is not None
        assert match_row[0] == "termo-legado"
        assert "MIGRAÇÃO" in match_row[1]

        # 7. a coluna antiga não existe mais
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_name = 'articles' AND column_name = 'search_term_id'
                """
            )
            assert cur.fetchone() is None

        # 8. canonical_link e content_hash foram preenchidos retroativamente (0009),
        # sem precisar rebuscar nada da rede
        with conn.cursor() as cur:
            cur.execute("SELECT canonical_link, content_hash FROM articles WHERE id = %s", (article_id,))
            canonical_link, content_hash = cur.fetchone()
        assert canonical_link is not None
        assert content_hash is not None
    finally:
        conn.close()


def test_run_migrations_is_idempotent(temp_db):
    conn = connect_to(temp_db)
    try:
        first = run_migrations(conn)
        assert len(first) > 0

        second = run_migrations(conn)
        assert second == []
    finally:
        conn.close()
