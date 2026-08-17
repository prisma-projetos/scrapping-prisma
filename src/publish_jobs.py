from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv

from jobs_publisher import publish_pending_jobs
from main import load_settings, setup_logging
from rabbitmq_client import PublishError, RabbitMQPublisher
from storage import get_connection, run_migrations

BASE_DIR = Path(__file__).resolve().parent.parent


def main() -> int:
    """Publica no RabbitMQ todos os jobs pendentes de analysis_jobs
    disponíveis no momento, e sai -- roda uma vez por execução, mesmo
    padrão do src/main.py, agendável via cron (ver README, seção
    "Publisher"). Não é um processo daemon/contínuo."""
    load_dotenv(BASE_DIR / ".env")

    settings = load_settings()
    logger = setup_logging(settings["logging"]["log_path"])

    conn = get_connection()
    applied = run_migrations(conn)
    if applied:
        logger.info("Migrations aplicadas nesta execução: %s", ", ".join(applied))

    try:
        try:
            with RabbitMQPublisher() as publisher:
                result = publish_pending_jobs(conn, publisher)
        except PublishError:
            logger.exception("Não foi possível conectar ao RabbitMQ -- nenhum job foi claimado nesta execução")
            return 1
    finally:
        conn.close()

    logger.info("Publicação de jobs concluída: %d publicados, %d falharam", result["published"], result["failed"])
    return 1 if result["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
