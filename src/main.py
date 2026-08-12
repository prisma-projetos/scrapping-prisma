from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml
from dotenv import load_dotenv

import articles_store
import url_utils
from article_extractor import extract_article, resolve_google_link
from rss_client import fetch_news_items
from search_terms_store import SearchTerm, SearchTermValidationError, load_and_validate, sync_search_terms
from storage import get_connection, run_migrations

BASE_DIR = Path(__file__).resolve().parent.parent
SEARCH_TERMS_PATH = BASE_DIR / "config" / "search_terms.yaml"
SETTINGS_PATH = BASE_DIR / "config" / "settings.yaml"


def load_settings() -> dict:
    with open(SETTINGS_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def setup_logging(log_path: str) -> logging.Logger:
    log_file = BASE_DIR / log_path
    log_file.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(log_file), logging.StreamHandler()],
    )
    return logging.getLogger("politica")


class ItemOutcome:
    __slots__ = ("new_article", "extraction_status", "new_match")

    def __init__(self, new_article: bool, extraction_status: str | None, new_match: bool):
        self.new_article = new_article
        self.extraction_status = extraction_status
        self.new_match = new_match


def _match_kwargs(term: SearchTerm) -> dict:
    return {
        "search_term_id": term.id,
        "matched_query": term.query,
        "matched_category": term.category,
        "matched_entity": term.entity,
        "matched_topic": term.topic,
        "matched_retrieval_type": term.retrieval_type,
    }


def _handle_item(conn, item, term: SearchTerm, settings: dict, processed_in_run: set[int]) -> ItemOutcome:
    now = datetime.now(timezone.utc)
    match_kwargs = _match_kwargs(term)

    # Atalho: se o guid já aponta para um artigo com extração bem-sucedida,
    # só registra o match — evita decodificar o link do Google à toa.
    if item.guid:
        existing = articles_store.find_by_guid(conn, item.guid)
        if existing and existing.extraction_status == "success":
            is_new_match = articles_store.upsert_match(conn, article_id=existing.id, collected_at=now, **match_kwargs)
            return ItemOutcome(new_article=False, extraction_status=None, new_match=is_new_match)

    resolved = resolve_google_link(item.google_link)
    canonical_provisional = url_utils.normalize_url(resolved.resolved_link) if resolved.resolved_link else None

    ref = articles_store.find_or_create_article(
        conn,
        google_guid=item.guid,
        canonical_link=canonical_provisional,
        title=item.title,
        google_link=item.google_link,
        resolved_link=resolved.resolved_link,
        source_name=item.source_name,
        published_at=item.published_at,
        collected_at=now,
    )

    # O mesmo artigo pode aparecer via dois termos na mesma execução — não
    # paga fetch/extração de novo, só registra o match adicional.
    seen_before_in_run = ref.id in processed_in_run
    processed_in_run.add(ref.id)

    if seen_before_in_run or ref.extraction_status == "success":
        is_new_match = articles_store.upsert_match(conn, article_id=ref.id, collected_at=now, **match_kwargs)
        new_article = ref.is_new and not seen_before_in_run
        return ItemOutcome(new_article=new_article, extraction_status=None, new_match=is_new_match)

    claimed = articles_store.claim_for_extraction(conn, ref.id)
    if not claimed:
        # Outra execução sobreposta já está processando (ou já terminou) este artigo.
        is_new_match = articles_store.upsert_match(conn, article_id=ref.id, collected_at=now, **match_kwargs)
        return ItemOutcome(new_article=ref.is_new, extraction_status=None, new_match=is_new_match)

    if resolved.resolved_link is None:
        articles_store.update_after_extraction(
            conn,
            ref.id,
            resolved_link=None,
            html_canonical_link=None,
            full_text=None,
            author=None,
            content_hash=None,
            extraction_status="failed",
            extraction_error=resolved.error,
        )
        is_new_match = articles_store.upsert_match(conn, article_id=ref.id, collected_at=now, **match_kwargs)
        return ItemOutcome(new_article=ref.is_new, extraction_status="failed", new_match=is_new_match)

    result = extract_article(resolved.resolved_link, settings)
    content_hash = articles_store.compute_content_hash(result.full_text) if result.full_text else None

    articles_store.update_after_extraction(
        conn,
        ref.id,
        resolved_link=result.resolved_link,
        html_canonical_link=result.html_canonical_link,
        full_text=result.full_text,
        author=result.author,
        content_hash=content_hash,
        extraction_status=result.status,
        extraction_error=result.error,
    )

    is_new_match = articles_store.upsert_match(conn, article_id=ref.id, collected_at=now, **match_kwargs)

    return ItemOutcome(new_article=ref.is_new, extraction_status=result.status, new_match=is_new_match)


def main() -> int:
    load_dotenv(BASE_DIR / ".env")

    settings = load_settings()
    logger = setup_logging(settings["logging"]["log_path"])

    conn = get_connection()
    applied = run_migrations(conn)
    if applied:
        logger.info("Migrations aplicadas nesta execução: %s", ", ".join(applied))
    else:
        logger.info("Nenhuma migration pendente")

    try:
        terms = load_and_validate(SEARCH_TERMS_PATH)
    except SearchTermValidationError as exc:
        logger.error("Configuração de termos de busca inválida:\n%s", "\n".join(exc.errors))
        conn.close()
        return 1

    sync_result = sync_search_terms(conn, terms)
    conn.commit()
    logger.info(
        "Sincronização de termos: %d inseridos, %d atualizados (%d no total, %d habilitados)",
        sync_result.inserted,
        sync_result.updated,
        len(terms),
        sum(1 for t in terms if t.enabled),
    )

    enabled_terms = [t for t in terms if t.enabled]

    processed_in_run: set[int] = set()
    status_counts: dict[str, int] = {}
    new_articles = 0
    new_matches = 0
    existing_matches = 0
    had_error = False

    try:
        for term in enabled_terms:
            logger.info("Buscando termo '%s' (%s)", term.query, term.id)
            try:
                items = fetch_news_items(term.id, term.query, settings["rss"], settings["scraping"]["user_agent"])
            except Exception:
                logger.exception("Falha ao buscar RSS para o termo '%s'", term.id)
                had_error = True
                continue

            logger.info("Termo '%s': %d itens retornados pelo RSS", term.id, len(items))

            for item in items:
                try:
                    outcome = _handle_item(conn, item, term, settings, processed_in_run)
                except Exception:
                    logger.exception("Falha ao processar item '%s' do termo '%s'", item.title[:80], term.id)
                    conn.rollback()
                    had_error = True
                    continue

                if outcome.new_article:
                    new_articles += 1
                if outcome.new_match:
                    new_matches += 1
                else:
                    existing_matches += 1
                if outcome.extraction_status:
                    status_counts[outcome.extraction_status] = status_counts.get(outcome.extraction_status, 0) + 1
    finally:
        conn.close()

    logger.info(
        "Resumo: %d artigos novos, %d matches novos, %d matches já existentes. Status de extração: %s",
        new_articles,
        new_matches,
        existing_matches,
        status_counts,
    )

    return 1 if had_error else 0


if __name__ == "__main__":
    sys.exit(main())
