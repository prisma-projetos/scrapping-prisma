from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import lxml.html

_TRACKING_PARAM_PREFIXES = ("utm_",)
_TRACKING_PARAM_NAMES = {
    "gclid",
    "fbclid",
    "msclkid",
    "mc_cid",
    "mc_eid",
    "igshid",
    "yclid",
    "spm",
    "ref_src",
    "ref",
    "_ga",
    "_gl",
}


def _is_tracking_param(name: str) -> bool:
    lowered = name.lower()
    return lowered.startswith(_TRACKING_PARAM_PREFIXES) or lowered in _TRACKING_PARAM_NAMES


def normalize_url(url: str) -> str:
    """Normaliza uma URL para uso como chave de deduplicação.

    Minúsculas em esquema/domínio, remove fragmento e porta padrão
    explícita, remove parâmetros de rastreamento conhecidos (utm_*, gclid,
    fbclid...) preservando os demais, ordenados para estabilidade.
    """
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()

    if scheme == "http" and netloc.endswith(":80"):
        netloc = netloc[: -len(":80")]
    elif scheme == "https" and netloc.endswith(":443"):
        netloc = netloc[: -len(":443")]

    query_params = sorted(
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not _is_tracking_param(key)
    )
    query = urlencode(query_params)

    path = parts.path or "/"

    return urlunsplit((scheme, netloc, path, query, ""))


def extract_canonical_from_html(html: bytes | str, base_url: str) -> str | None:
    """Extrai `<link rel="canonical">` do HTML, se presente e válido.

    Aceita `bytes` (preferível) ou `str`. Com `bytes`, quem detecta o
    encoding é o próprio parser HTML do lxml/libxml2 (via BOM ou
    `<meta charset>` no próprio documento) — não duplicamos aqui a lógica
    de detecção de charset que o `trafilatura` já faz em `article_extractor`.
    Passar `str` (já decodificado por outra fonte, ex. `response.text`) é
    o que causava mojibake quando o site não declarava charset no header
    HTTP e o `requests` caía no fallback Latin-1.

    Só aceita o valor se, resolvido contra `base_url`, resultar numa URL
    absoluta http(s) — qualquer outra coisa (relativa quebrada, esquema
    estranho, vazio) é ignorada.
    """
    try:
        tree = lxml.html.fromstring(html)
    except Exception:
        return None

    for link in tree.xpath("//link[@rel]"):
        rel = (link.get("rel") or "").strip().lower()
        if rel != "canonical":
            continue

        href = (link.get("href") or "").strip()
        if not href:
            continue

        absolute = urljoin(base_url, href)
        parsed = urlsplit(absolute)
        if parsed.scheme in ("http", "https") and parsed.netloc:
            return absolute

    return None
