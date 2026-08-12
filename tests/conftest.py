from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg
import pytest
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

load_dotenv(PROJECT_ROOT / ".env")

from storage import run_migrations  # noqa: E402  (precisa do sys.path acima)


def admin_connect(dbname: str) -> psycopg.Connection:
    """Conexão com autocommit — necessária para comandos que não rodam
    dentro de transação, como CREATE DATABASE / DROP DATABASE."""
    return psycopg.connect(
        host=os.environ["POSTGRES_HOST"],
        port=os.environ["POSTGRES_PORT"],
        dbname=dbname,
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        autocommit=True,
    )


def connect_to(dbname: str) -> psycopg.Connection:
    return psycopg.connect(
        host=os.environ["POSTGRES_HOST"],
        port=os.environ["POSTGRES_PORT"],
        dbname=dbname,
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
    )


def create_database(name: str) -> None:
    admin_conn = admin_connect(os.environ["POSTGRES_DB"])
    try:
        with admin_conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,))
            if cur.fetchone() is None:
                cur.execute(f'CREATE DATABASE "{name}"')
    finally:
        admin_conn.close()


def drop_database(name: str) -> None:
    admin_conn = admin_connect(os.environ["POSTGRES_DB"])
    try:
        with admin_conn.cursor() as cur:
            cur.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s AND pid <> pg_backend_pid()",
                (name,),
            )
            cur.execute(f'DROP DATABASE IF EXISTS "{name}"')
    finally:
        admin_conn.close()


@pytest.fixture(scope="session", autouse=True)
def _test_database_ready():
    """Garante o banco de teste principal (POSTGRES_TEST_DB) existente e
    com todas as migrations aplicadas, uma vez por sessão de testes."""
    test_db = os.environ["POSTGRES_TEST_DB"]
    create_database(test_db)
    conn = connect_to(test_db)
    try:
        run_migrations(conn)
    finally:
        conn.close()


@pytest.fixture()
def db_conn():
    """Conexão com o banco de teste, com as tabelas de dados limpas antes
    de cada teste (schema_migrations não é tocado, para não reaplicar
    migrations à toa)."""
    conn = connect_to(os.environ["POSTGRES_TEST_DB"])
    with conn.cursor() as cur:
        cur.execute("TRUNCATE TABLE analysis_jobs, article_search_matches, articles, search_terms RESTART IDENTITY CASCADE")
    conn.commit()
    try:
        yield conn
    finally:
        conn.close()
