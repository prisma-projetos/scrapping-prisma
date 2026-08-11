from __future__ import annotations

from dataclasses import dataclass

import trafilatura
from googlenewsdecoder import gnewsdecoder

from scraping_utils import BlockedError, fetch_with_retry, is_allowed_by_robots, sleep_with_jitter


@dataclass
class ExtractionResult:
    resolved_link: str | None
    full_text: str | None
    status: str  # success / blocked / paywalled / failed / skipped_robots
    error: str | None


def extract_article(google_link: str, settings: dict) -> ExtractionResult:
    scraping = settings["scraping"]
    user_agent = scraping["user_agent"]
    timeout_seconds = scraping["timeout_seconds"]

    # O link do RSS do Google News não é mais um redirect HTTP simples: é uma
    # página interstitial cujo destino real só sai decodificando um token
    # assinado pelo próprio Google (é assim que o feed público funciona hoje,
    # não é um mecanismo de segurança sendo contornado).
    try:
        decoded = gnewsdecoder(google_link, interval=1)
    except Exception as exc:
        return ExtractionResult(resolved_link=None, full_text=None, status="failed", error=f"falha ao decodificar link: {exc}")

    if not decoded.get("status"):
        return ExtractionResult(
            resolved_link=None, full_text=None, status="failed", error=f"falha ao decodificar link: {decoded.get('message')}"
        )

    resolved_link = decoded["decoded_url"]

    if not is_allowed_by_robots(resolved_link, user_agent, timeout_seconds):
        return ExtractionResult(resolved_link=resolved_link, full_text=None, status="skipped_robots", error=None)

    sleep_with_jitter(scraping["min_delay_seconds"], scraping["max_delay_seconds"])

    try:
        response = fetch_with_retry(
            resolved_link,
            user_agent=user_agent,
            timeout_seconds=timeout_seconds,
            max_retries=scraping["max_retries"],
        )
    except BlockedError as exc:
        return ExtractionResult(resolved_link=resolved_link, full_text=None, status="blocked", error=str(exc))
    except Exception as exc:
        return ExtractionResult(resolved_link=resolved_link, full_text=None, status="failed", error=str(exc))

    final_url = response.url
    text = trafilatura.extract(response.text, url=final_url)

    if not text:
        return ExtractionResult(resolved_link=final_url, full_text=None, status="failed", error="trafilatura não extraiu conteúdo")

    if len(text) < scraping["paywall_min_chars"]:
        return ExtractionResult(resolved_link=final_url, full_text=text, status="paywalled", error=None)

    return ExtractionResult(resolved_link=final_url, full_text=text, status="success", error=None)
