from __future__ import annotations

import random
import time
import urllib.robotparser
from urllib.parse import urlparse

import requests

_robots_cache: dict[str, urllib.robotparser.RobotFileParser] = {}


class BlockedError(Exception):
    """Bloqueio explícito do site (HTTP 403/429) — não deve ser tentado de novo."""


def sleep_with_jitter(min_seconds: float, max_seconds: float) -> None:
    time.sleep(random.uniform(min_seconds, max_seconds))


def is_allowed_by_robots(url: str, user_agent: str, timeout_seconds: int = 10) -> bool:
    parsed = urlparse(url)
    domain = f"{parsed.scheme}://{parsed.netloc}"

    parser = _robots_cache.get(domain)
    if parser is None:
        parser = urllib.robotparser.RobotFileParser()
        # Busca o robots.txt via `requests` (mesma stack HTTP/TLS usada no resto
        # do projeto) em vez do `.read()` nativo do robotparser, que usa
        # `urllib.request` puro e pode falhar de forma diferente (ex.: cadeia de
        # certificados TLS não configurada no ambiente) sem que isso tenha
        # qualquer relação com a política real do site.
        try:
            response = requests.get(f"{domain}/robots.txt", headers={"User-Agent": user_agent}, timeout=timeout_seconds)
            if response.status_code >= 400:
                # Sem robots.txt (ou erro do site ao servi-lo): não há regra publicada, segue permitido.
                parser.allow_all = True
            else:
                parser.parse(response.text.splitlines())
        except requests.RequestException:
            # Falha de rede/TLS ao buscar o robots.txt não deve travar a coleta
            # inteira por um problema de infraestrutura local — segue permissivo.
            parser.allow_all = True
        _robots_cache[domain] = parser

    return parser.can_fetch(user_agent, url)


def fetch_with_retry(
    url: str,
    *,
    user_agent: str,
    timeout_seconds: int,
    max_retries: int,
) -> requests.Response:
    headers = {"User-Agent": user_agent}
    last_exception: Exception | None = None

    for attempt in range(max_retries):
        try:
            response = requests.get(url, headers=headers, timeout=timeout_seconds, allow_redirects=True)
        except requests.RequestException as exc:
            last_exception = exc
            time.sleep(2**attempt)
            continue

        if response.status_code in (403, 429):
            raise BlockedError(f"HTTP {response.status_code} em {url}")

        if response.status_code >= 500:
            last_exception = requests.HTTPError(f"HTTP {response.status_code} em {url}")
            time.sleep(2**attempt)
            continue

        response.raise_for_status()
        return response

    raise last_exception or RuntimeError(f"Falha ao buscar {url} após {max_retries} tentativas")
