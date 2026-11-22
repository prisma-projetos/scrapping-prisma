from __future__ import annotations

from unittest.mock import patch

import requests

from article_extractor import extract_article

# Config mínima de scraping para os testes: sem delay (testes rápidos e
# determinísticos) e paywall_min_chars baixo o suficiente pro parágrafo de
# teste não ser classificado como paywall.
SETTINGS = {
    "scraping": {
        "user_agent": "TesteScraper/1.0",
        "timeout_seconds": 5,
        "min_delay_seconds": 0,
        "max_delay_seconds": 0,
        "max_retries": 1,
        "paywall_min_chars": 10,
    }
}

ARTICLE_URL = "https://exemplo.com/noticia-tarcisio"

UTF8_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="pt-BR">
<head><title>Noticia</title></head>
<body>
<article>
<h1>Governador Tarcisio de Freitas anuncia medida</h1>
<p>{paragraph}</p>
</article>
</body>
</html>"""


def _build_response(url: str, body: bytes, content_type: str | None) -> requests.Response:
    """Monta um `requests.Response` de verdade (não um Mock genérico) pra
    reproduzir fielmente a resolução de encoding que o `requests` faz na
    prática: `response.encoding` é derivado do header `Content-Type` do
    mesmo jeito que `HTTPAdapter.build_response` faz — inclusive o
    fallback pra Latin-1 (RFC 2616) quando não há charset declarado, que é
    exatamente a causa do bug de mojibake. `content_type=None` simula
    resposta sem o header `Content-Type` de jeito nenhum."""
    response = requests.Response()
    response.status_code = 200
    response.url = url
    response._content = body
    if content_type is not None:
        response.headers["Content-Type"] = content_type
    response.encoding = requests.utils.get_encoding_from_headers(response.headers)
    return response


def _long_paragraph(sentence: str, repeat: int = 30) -> str:
    """Repete a frase para passar do `paywall_min_chars` e dar ao
    trafilatura um bloco de texto substancial o bastante para reconhecer
    como conteúdo do artigo, não boilerplate."""
    return " ".join([sentence] * repeat)


def test_extract_article_decodes_utf8_body_without_charset_header():
    """Reproduz o bug real: o site serve HTML em UTF-8 mas não declara
    charset no header `Content-Type`. Sem o fix (usando `response.text`),
    o `requests` assume Latin-1 e o texto extraído vem com mojibake
    ('TarcÃsio' em vez de 'Tarcísio')."""
    paragraph = _long_paragraph(
        "O governador Tarcísio de Freitas discursou sobre a situação econômica de São Paulo."
    )
    html = UTF8_HTML_TEMPLATE.format(paragraph=paragraph)
    body = html.encode("utf-8")
    response = _build_response(ARTICLE_URL, body, "text/html")  # sem charset declarado

    with (
        patch("article_extractor.is_allowed_by_robots", return_value=True),
        patch("article_extractor.fetch_with_retry", return_value=response),
    ):
        result = extract_article(ARTICLE_URL, SETTINGS)

    assert result.status == "success"
    assert result.full_text is not None
    assert "Tarcísio" in result.full_text
    assert "São Paulo" in result.full_text
    # "Ã" é a marca registrada do mojibake UTF-8 decodificado como Latin-1
    assert "Ã" not in result.full_text


def test_extract_article_respects_declared_latin1_charset():
    """Guarda contra regressão do caso oposto: quando o site declara
    Latin-1 explicitamente no header `Content-Type` (charset=ISO-8859-1)
    e o corpo é de fato Latin-1, a extração precisa confiar nessa
    declaração via `response.text` — e não passar pelos bytes crus, onde
    o `charset_normalizer` (heurística estatística usada por
    lxml/trafilatura) pode confundir Latin-1 com codepages parecidos da
    Europa Central (ex.: cp1250) justamente nas vogais nasais 'ã'/'õ'
    (comportamento verificado manualmente: sem essa mitigação, 'São
    Paulo'/'situação' viravam 'Săo Paulo'/'situaçăo'). Como o charset foi
    declarado de propósito pelo site, aqui a fidelidade tem que ser
    completa, incluindo ã/õ."""
    paragraph = _long_paragraph(
        "O governador Tarcísio de Freitas discursou sobre a situação econômica de São Paulo."
    )
    html = UTF8_HTML_TEMPLATE.format(paragraph=paragraph)
    body = html.encode("latin-1")
    response = _build_response(ARTICLE_URL, body, "text/html; charset=ISO-8859-1")

    with (
        patch("article_extractor.is_allowed_by_robots", return_value=True),
        patch("article_extractor.fetch_with_retry", return_value=response),
    ):
        result = extract_article(ARTICLE_URL, SETTINGS)

    assert result.status == "success"
    assert result.full_text is not None
    assert "Tarcísio" in result.full_text
    assert "São Paulo" in result.full_text
    assert "situação econômica" in result.full_text


def test_extract_article_falls_back_to_bytes_when_content_type_header_is_absent():
    """Terceiro caso do sinal híbrido: quando não há header `Content-Type`
    nenhum (não é só "sem charset", é o header inteiro ausente), não há
    charset declarado pra confiar — o caminho tem que continuar sendo o
    dos bytes crus (mesma correção do primeiro teste), não `response.text`
    (que aqui nem teria como aplicar o fallback Latin-1 do `requests`,
    já que `get_encoding_from_headers` retorna `None` sem Content-Type,
    mas o ponto é garantir que a ausência total do header também é
    tratada como 'charset não declarado')."""
    paragraph = _long_paragraph(
        "O governador Tarcísio de Freitas discursou sobre a situação econômica de São Paulo."
    )
    html = UTF8_HTML_TEMPLATE.format(paragraph=paragraph)
    body = html.encode("utf-8")
    response = _build_response(ARTICLE_URL, body, content_type=None)

    with (
        patch("article_extractor.is_allowed_by_robots", return_value=True),
        patch("article_extractor.fetch_with_retry", return_value=response),
    ):
        result = extract_article(ARTICLE_URL, SETTINGS)

    assert result.status == "success"
    assert result.full_text is not None
    assert "Tarcísio" in result.full_text
    assert "São Paulo" in result.full_text
    assert "Ã" not in result.full_text
