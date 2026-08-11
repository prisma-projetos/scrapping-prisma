from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml
from dotenv import load_dotenv

from article_extractor import extract_article
from rss_client import fetch_news_items
from storage import apply_schema, get_connection, guid_exists, save_article

BASE_DIR = Path(__file__).resolve().parent.parent


def load_config() -> tuple[list[dict], dict]:
    with open(BASE_DIR / "config" / "search_terms.yaml", encoding="utf-8") as f:
        search_terms = yaml.safe_load(f)["search_terms"]
    with open(BASE_DIR / "config" / "settings.yaml", encoding="utf-8") as f:
        settings = yaml.safe_load(f)
    return search_terms, settings


def setup_logging(log_path: str) -> logging.Logger:
    log_file = BASE_DIR / log_path
    log_file.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(log_file), logging.StreamHandler()],
    )
    return logging.getLogger("politica")


def main() -> int:
    load_dotenv(BASE_DIR / ".env")
    search_terms, settings = load_config()
    logger = setup_logging(settings["logging"]["log_path"])

    conn = get_connection()
    apply_schema(conn)

    status_counts: dict[str, int] = {}
    new_items = 0
    had_error = False

    try:
        for term in search_terms:
            if not term.get("enabled", True):
                continue

            logger.info("Buscando termo '%s' (%s)", term["query"], term["id"])
            try:
                items = fetch_news_items(
                    term["id"], term["query"], settings["rss"], settings["scraping"]["user_agent"]
                )
            except Exception:
                logger.exception("Falha ao buscar RSS para o termo '%s'", term["id"])
                had_error = True
                continue

            for item in items:
                if guid_exists(conn, item.guid):
                    continue

                new_items += 1
                result = extract_article(item.google_link, settings)
                status_counts[result.status] = status_counts.get(result.status, 0) + 1

                save_article(
                    conn,
                    {
                        "guid": item.guid,
                        "search_term_id": item.search_term_id,
                        "title": item.title,
                        "google_link": item.google_link,
                        "resolved_link": result.resolved_link,
                        "source_name": item.source_name,
                        "published_at": item.published_at,
                        "collected_at": datetime.now(timezone.utc),
                        "full_text": result.full_text,
                        "extraction_status": result.status,
                        "extraction_error": result.error,
                    },
                )

                logger.info("Artigo '%s' -> %s", item.title[:80], result.status)
    finally:
        conn.close()

    logger.info("Resumo: %d itens novos coletados. Status: %s", new_items, status_counts)

    return 1 if had_error else 0


if __name__ == "__main__":
    sys.exit(main())
