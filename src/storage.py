from __future__ import annotations

import importlib.util
import logging
import os
from pathlib import Path

import psycopg

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "db" / "migrations"

logger = logging.getLogger("politica")


def get_connection(*, dbname: str | None = None, autocommit: bool = False) -> psycopg.Connection:
    conn = psycopg.connect(
        host=os.environ["POSTGRES_HOST"],
        port=os.environ["POSTGRES_PORT"],
        dbname=dbname or os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
    )
    if autocommit:
        conn.autocommit = True
    return conn


def _ensure_migrations_table(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
    conn.commit()


def _applied_versions(conn: psycopg.Connection) -> set[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT version FROM schema_migrations")
        return {row[0] for row in cur.fetchall()}


def _load_py_migration(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def run_migrations(conn: psycopg.Connection, *, up_to: str | None = None) -> list[str]:
    """Aplica as migrations pendentes de db/migrations/, em ordem pelo
    prefixo numérico do nome do arquivo. Cada migration (.sql ou .py) roda
    na mesma transação do seu registro em schema_migrations — se a
    migration falhar, nada é marcado como aplicado. Migrations .py expõem
    `def migrate(conn)` e não devem chamar commit()/rollback() por conta
    própria; quem comita é este runner.

    `up_to`, se informado (ex.: "0001_baseline"), para depois de aplicar
    essa versão (inclusive) — usado por testes que precisam simular um
    banco "no meio da migração" antes de aplicar o restante.

    Retorna a lista de versões efetivamente aplicadas nesta chamada (vazia
    se já estava tudo em dia).
    """
    _ensure_migrations_table(conn)
    applied = _applied_versions(conn)

    pending_paths = sorted(
        p for p in MIGRATIONS_DIR.iterdir() if p.suffix in (".sql", ".py") and p.stem not in applied
    )
    if up_to is not None:
        pending_paths = [p for p in pending_paths if p.stem <= up_to]

    applied_now: list[str] = []
    for path in pending_paths:
        logger.info("Aplicando migration %s", path.name)
        if path.suffix == ".sql":
            sql = path.read_text(encoding="utf-8")
            with conn.cursor() as cur:
                cur.execute(sql)
        else:
            module = _load_py_migration(path)
            module.migrate(conn)

        with conn.cursor() as cur:
            cur.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (path.stem,))
        conn.commit()
        applied_now.append(path.stem)

    return applied_now
