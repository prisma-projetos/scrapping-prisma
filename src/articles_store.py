from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime

import psycopg
from psycopg import errors as pg_errors

# Estados que uma execução futura pode tentar extrair de novo. `blocked` e
# `paywalled` ficam de fora de propósito: repetir a extração não muda o
# resultado (bloqueio explícito / paywall não desaparecem sozinhos).
RECOVERABLE_STATUSES = ("pending", "processing", "failed", "empty", "skipped_robots")

_WHITESPACE_RE = re.compile(r"\s+")


def compute_content_hash(text: str) -> str:
    """SHA-256 sobre uma versão normalizada do texto (Unicode NFC, espaços
    e quebras de linha colapsados, sem espaços nas pontas). O texto
    original armazenado em `full_text` nunca é alterado — a normalização
    existe só para calcular este hash."""
    normalized = unicodedata.normalize("NFC", text)
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    normalized = _WHITESPACE_RE.sub(" ", normalized).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ArticleRef:
    id: int
    extraction_status: str
    is_new: bool


def find_by_guid(conn: psycopg.Connection, google_guid: str) -> ArticleRef | None:
    """Busca direta e barata por google_guid, usada como atalho antes de
    decodificar o link do Google — evita gastar uma chamada de rede
    (gnewsdecoder) num artigo que já sabemos que terminou com sucesso."""
    with conn.cursor() as cur:
        cur.execute("SELECT id, extraction_status FROM articles WHERE google_guid = %s", (google_guid,))
        row = cur.fetchone()
    if row is None:
        return None
    article_id, status = row
    return ArticleRef(id=article_id, extraction_status=status, is_new=False)


def _select_existing(conn: psycopg.Connection, google_guid: str | None, canonical_link: str | None):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, extraction_status, google_guid, canonical_link
            FROM articles
            WHERE (%(guid)s::text IS NOT NULL AND google_guid = %(guid)s::text)
               OR (%(canonical)s::text IS NOT NULL AND canonical_link = %(canonical)s::text)
            LIMIT 1
            """,
            {"guid": google_guid, "canonical": canonical_link},
        )
        return cur.fetchone()


def find_or_create_article(
    conn: psycopg.Connection,
    *,
    google_guid: str | None,
    canonical_link: str | None,
    title: str,
    google_link: str,
    resolved_link: str | None,
    source_name: str | None,
    published_at: datetime | None,
    collected_at: datetime,
) -> ArticleRef:
    """Localiza um artigo existente por `google_guid` OU `canonical_link`
    (nunca um `ON CONFLICT` mirando uma única constraint por vez — o mesmo
    artigo pode chegar com guids diferentes em buscas diferentes, então um
    branch por "qual chave está disponível" pode tentar inserir uma
    segunda linha e estourar violação na constraint não coberta). Se não
    achar, insere; se a inserção colidir (corrida entre execuções
    concorrentes), refaz a busca e usa a linha que venceu a corrida.

    Nunca sobrescreve `google_guid`/`canonical_link` já preenchidos — só
    completa o que estava faltando."""
    row = _select_existing(conn, google_guid, canonical_link)
    if row is not None:
        article_id, status, existing_guid, existing_canonical = row
        updates: dict = {}
        if google_guid and not existing_guid:
            updates["google_guid"] = google_guid
        if canonical_link and not existing_canonical:
            updates["canonical_link"] = canonical_link
        if updates:
            set_clause = ", ".join(f"{col} = %({col})s" for col in updates)
            updates["article_id"] = article_id
            with conn.cursor() as cur:
                cur.execute(f"UPDATE articles SET {set_clause} WHERE id = %(article_id)s", updates)
            conn.commit()
        return ArticleRef(id=article_id, extraction_status=status, is_new=False)

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO articles (
                    google_guid, canonical_link, title, google_link, resolved_link,
                    source_name, published_at, collected_at, extraction_status
                ) VALUES (
                    %(google_guid)s, %(canonical_link)s, %(title)s, %(google_link)s, %(resolved_link)s,
                    %(source_name)s, %(published_at)s, %(collected_at)s, 'pending'
                )
                RETURNING id, extraction_status
                """,
                {
                    "google_guid": google_guid,
                    "canonical_link": canonical_link,
                    "title": title,
                    "google_link": google_link,
                    "resolved_link": resolved_link,
                    "source_name": source_name,
                    "published_at": published_at,
                    "collected_at": collected_at,
                },
            )
            article_id, status = cur.fetchone()
        conn.commit()
        return ArticleRef(id=article_id, extraction_status=status, is_new=True)
    except pg_errors.UniqueViolation:
        conn.rollback()
        row = _select_existing(conn, google_guid, canonical_link)
        if row is None:
            raise
        article_id, status, _, _ = row
        return ArticleRef(id=article_id, extraction_status=status, is_new=False)


@dataclass(frozen=True)
class ArticleForPublishing:
    id: int
    title: str
    full_text: str | None
    canonical_link: str | None
    source_name: str | None
    published_at: datetime | None


def find_for_publishing(conn: psycopg.Connection, article_id: int) -> ArticleForPublishing | None:
    """Busca só os campos que o publisher (fila de análise -> RabbitMQ,
    ver src/jobs_publisher.py) precisa embutir no payload publicado -- o
    Classifier nunca acessa este Postgres, então o conteúdo precisa viajar
    na mensagem, não só o `article_id`."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, title, full_text, canonical_link, source_name, published_at
            FROM articles
            WHERE id = %s
            """,
            (article_id,),
        )
        row = cur.fetchone()
    if row is None:
        return None
    article_id, title, full_text, canonical_link, source_name, published_at = row
    return ArticleForPublishing(
        id=article_id,
        title=title,
        full_text=full_text,
        canonical_link=canonical_link,
        source_name=source_name,
        published_at=published_at,
    )


def claim_for_extraction(conn: psycopg.Connection, article_id: int) -> bool:
    """Marca o artigo como 'processing' atomicamente, só se ele estava num
    estado recuperável. Retorna False se outro processo/execução já
    claimou (ou o artigo virou 'success' nesse meio tempo) — quem chamar
    deve pular a extração nesse caso, sem tratar como erro."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE articles
            SET extraction_status = 'processing'
            WHERE id = %s AND extraction_status = ANY(%s)
            RETURNING id
            """,
            (article_id, list(RECOVERABLE_STATUSES)),
        )
        claimed = cur.fetchone() is not None
    conn.commit()
    return claimed


def update_after_extraction(
    conn: psycopg.Connection,
    article_id: int,
    *,
    resolved_link: str | None,
    html_canonical_link: str | None,
    full_text: str | None,
    author: str | None,
    content_hash: str | None,
    extraction_status: str,
    extraction_error: str | None,
) -> None:
    """Não recebe `canonical_link`: a identidade de dedup não muda depois
    do fetch (ver find_or_create_article). `extraction_error` é limpo
    automaticamente quando o novo status é 'success'."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE articles
            SET resolved_link = COALESCE(%(resolved_link)s, resolved_link),
                html_canonical_link = %(html_canonical_link)s,
                full_text = %(full_text)s,
                author = %(author)s,
                content_hash = %(content_hash)s,
                extraction_status = %(extraction_status)s,
                extraction_error = CASE WHEN %(extraction_status)s = 'success' THEN NULL ELSE %(extraction_error)s END
            WHERE id = %(article_id)s
            """,
            {
                "article_id": article_id,
                "resolved_link": resolved_link,
                "html_canonical_link": html_canonical_link,
                "full_text": full_text,
                "author": author,
                "content_hash": content_hash,
                "extraction_status": extraction_status,
                "extraction_error": extraction_error,
            },
        )
    conn.commit()


def upsert_match(
    conn: psycopg.Connection,
    *,
    article_id: int,
    search_term_id: str,
    matched_query: str,
    matched_category: str,
    matched_entity: str | None,
    matched_topic: str | None,
    matched_retrieval_type: str,
    collected_at: datetime,
) -> bool:
    """Registra que este termo encontrou este artigo, preservando o
    snapshot dos campos do termo no momento da coleta. Retorna True se
    criou uma relação nova, False se já existia (idempotente)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO article_search_matches (
                article_id, search_term_id, query_snapshot, category_snapshot,
                entity_snapshot, topic_snapshot, retrieval_type_snapshot, matched_at
            ) VALUES (
                %(article_id)s, %(search_term_id)s, %(query)s, %(category)s,
                %(entity)s, %(topic)s, %(retrieval_type)s, %(matched_at)s
            )
            ON CONFLICT (article_id, search_term_id) DO NOTHING
            RETURNING id
            """,
            {
                "article_id": article_id,
                "search_term_id": search_term_id,
                "query": matched_query,
                "category": matched_category,
                "entity": matched_entity,
                "topic": matched_topic,
                "retrieval_type": matched_retrieval_type,
                "matched_at": collected_at,
            },
        )
        created = cur.fetchone() is not None
    conn.commit()
    return created
