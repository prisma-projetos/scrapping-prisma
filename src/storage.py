from __future__ import annotations

import os
from pathlib import Path

import psycopg

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "db" / "schema.sql"


def get_connection() -> psycopg.Connection:
    return psycopg.connect(
        host=os.environ["POSTGRES_HOST"],
        port=os.environ["POSTGRES_PORT"],
        dbname=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
    )


def apply_schema(conn: psycopg.Connection) -> None:
    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    with conn.cursor() as cur:
        cur.execute(schema_sql)
    conn.commit()


def guid_exists(conn: psycopg.Connection, guid: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM articles WHERE guid = %s", (guid,))
        return cur.fetchone() is not None


def save_article(conn: psycopg.Connection, article: dict) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO articles (
                guid, search_term_id, title, google_link, resolved_link,
                source_name, published_at, collected_at, full_text,
                extraction_status, extraction_error
            ) VALUES (
                %(guid)s, %(search_term_id)s, %(title)s, %(google_link)s, %(resolved_link)s,
                %(source_name)s, %(published_at)s, %(collected_at)s, %(full_text)s,
                %(extraction_status)s, %(extraction_error)s
            )
            ON CONFLICT (guid) DO NOTHING
            """,
            article,
        )
    conn.commit()
