from __future__ import annotations

from dataclasses import dataclass

import trafilatura
from googlenewsdecoder import gnewsdecoder

from scraping_utils import BlockedError, fetch_with_retry, is_allowed_by_robots, sleep_with_jitter
from url_utils import extract_canonical_from_html


@dataclass
class ResolvedLink:
    resolved_link: str | None
    error: str | None


def resolve_google_link(google_link: str, *, interval: int = 1) -> ResolvedLink:
    """Decodifica o link ofuscado do RSS do Google News para a URL real do
    veículo. Não é mais um redirect HTTP simples desde uma mudança do
    Google em 2024 — é assim que o feed público funciona hoje, não é um
    mecanismo de segurança sendo contornado. Separado de `extract_article`
    porque precisa rodar *antes* do find-or-create (para calcular o
    `canonical_link` usado na deduplicação) e não deve ser chamado duas
    vezes para o mesmo item."""
    try:
        decoded = gnewsdecoder(google_link, interval=interval)
    except Exception as exc:
        return ResolvedLink(resolved_link=None, error=f"falha ao decodificar link: {exc}")

    if not decoded.get("status"):
        return ResolvedLink(resolved_link=None, error=f"falha ao decodificar link: {decoded.get('message')}")

    return ResolvedLink(resolved_link=decoded["decoded_url"], error=None)


def _has_declared_charset(headers) -> bool:
    """True se o header `Content-Type` da resposta declarar um parâmetro
    `charset` explicitamente.

    Isso importa porque `requests` (via `get_encoding_from_headers`)
    retorna "ISO-8859-1" tanto quando o site declarou esse charset de
    propósito quanto quando não declarou charset nenhum e o content-type
    é `text/*` — nesse segundo caso é só o fallback do RFC 2616 aplicado
    às cegas, não uma informação real do site. `response.encoding` sozinho
    não distingue os dois casos. O sinal confiável é olhar o header bruto:
    o parâmetro `charset=` só aparece ali quando foi mesmo declarado.
    """
    content_type = headers.get("Content-Type", "")
    return "charset=" in content_type.lower()


@dataclass
class ExtractionResult:
    resolved_link: str | None
    html_canonical_link: str | None
    full_text: str | None
    author: str | None
    status: str  # success / paywalled / blocked / failed / skipped_robots / empty
    error: str | None


def extract_article(resolved_link: str, settings: dict) -> ExtractionResult:
    """Recebe o link já resolvido (ver `resolve_google_link`) — não
    decodifica nada aqui, só verifica robots.txt, respeita o rate limit,
    busca a página e extrai o conteúdo."""
    scraping = settings["scraping"]
    user_agent = scraping["user_agent"]
    timeout_seconds = scraping["timeout_seconds"]

    if not is_allowed_by_robots(resolved_link, user_agent, timeout_seconds):
        return ExtractionResult(
            resolved_link=resolved_link,
            html_canonical_link=None,
            full_text=None,
            author=None,
            status="skipped_robots",
            error=None,
        )

    sleep_with_jitter(scraping["min_delay_seconds"], scraping["max_delay_seconds"])

    try:
        response = fetch_with_retry(
            resolved_link,
            user_agent=user_agent,
            timeout_seconds=timeout_seconds,
            max_retries=scraping["max_retries"],
        )
    except BlockedError as exc:
        return ExtractionResult(
            resolved_link=resolved_link,
            html_canonical_link=None,
            full_text=None,
            author=None,
            status="blocked",
            error=str(exc),
        )
    except Exception as exc:
        return ExtractionResult(
            resolved_link=resolved_link,
            html_canonical_link=None,
            full_text=None,
            author=None,
            status="failed",
            error=str(exc),
        )

    final_url = response.url
    # Mitigação híbrida pro bug de mojibake (~9,1% dos artigos, ex.:
    # "TarcÃsio" em vez de "Tarcísio"):
    #
    # - Se o site declara charset explicitamente no `Content-Type`,
    #   confiamos nisso e usamos `response.text` (já decodificado
    #   corretamente pelo `requests` com o charset declarado) — evita o
    #   risco de o `charset_normalizer` (heurística estatística usada por
    #   lxml/trafilatura a partir dos bytes) confundir encodings de 1 byte
    #   parecidos entre si (ex.: Latin-1 com cp1250 nas vogais nasais ã/õ).
    # - Se o site NÃO declara charset, `requests` aplica Latin-1 como
    #   fallback cego (RFC 2616) mesmo quando o corpo real está em UTF-8 —
    #   causa raiz confirmada do bug. Nesse caso usamos `response.content`
    #   (bytes crus) e deixamos lxml/trafilatura detectarem o encoding real
    #   a partir do próprio conteúdo.
    if _has_declared_charset(response.headers):
        html_source = response.text
    else:
        html_source = response.content

    html_canonical_link = extract_canonical_from_html(html_source, final_url)

    document = trafilatura.bare_extraction(html_source, url=final_url, with_metadata=True)
    text = document.text if document else None
    author = document.author if document else None

    if not text:
        return ExtractionResult(
            resolved_link=final_url,
            html_canonical_link=html_canonical_link,
            full_text=None,
            author=author,
            status="empty",
            error="trafilatura não extraiu conteúdo",
        )

    if len(text) < scraping["paywall_min_chars"]:
        return ExtractionResult(
            resolved_link=final_url,
            html_canonical_link=html_canonical_link,
            full_text=text,
            author=author,
            status="paywalled",
            error=None,
        )

    return ExtractionResult(
        resolved_link=final_url,
        html_canonical_link=html_canonical_link,
        full_text=text,
        author=author,
        status="success",
        error=None,
    )
