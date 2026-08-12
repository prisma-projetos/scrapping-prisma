from url_utils import extract_canonical_from_html, normalize_url


def test_normalize_url_removes_tracking_params():
    url = "https://Example.com/noticia?utm_source=twitter&utm_campaign=x&gclid=abc&id=42"
    normalized = normalize_url(url)
    assert "utm_source" not in normalized
    assert "utm_campaign" not in normalized
    assert "gclid" not in normalized
    assert "id=42" in normalized


def test_normalize_url_preserves_non_tracking_params():
    url = "https://example.com/busca?categoria=politica&pagina=2"
    normalized = normalize_url(url)
    assert "categoria=politica" in normalized
    assert "pagina=2" in normalized


def test_normalize_url_removes_fragment():
    url = "https://example.com/noticia#comentarios"
    normalized = normalize_url(url)
    assert "#" not in normalized


def test_normalize_url_lowercases_scheme_and_host():
    url = "HTTPS://Example.COM/Noticia"
    normalized = normalize_url(url)
    assert normalized.startswith("https://example.com")
    # o path não é forçado a minúsculo (pode ser case-sensitive no servidor)
    assert "/Noticia" in normalized


def test_normalize_url_is_deterministic_regardless_of_param_order():
    url_a = "https://example.com/x?b=2&a=1"
    url_b = "https://example.com/x?a=1&b=2"
    assert normalize_url(url_a) == normalize_url(url_b)


def test_extract_canonical_from_html_accepts_valid_absolute_url():
    html = '<html><head><link rel="canonical" href="https://example.com/artigo-real"></head></html>'
    result = extract_canonical_from_html(html, base_url="https://example.com/artigo-real?utm_source=x")
    assert result == "https://example.com/artigo-real"


def test_extract_canonical_from_html_resolves_relative_url():
    html = '<html><head><link rel="canonical" href="/artigo-real"></head></html>'
    result = extract_canonical_from_html(html, base_url="https://example.com/qualquer-coisa")
    assert result == "https://example.com/artigo-real"


def test_extract_canonical_from_html_rejects_non_http_scheme():
    html = '<html><head><link rel="canonical" href="javascript:alert(1)"></head></html>'
    result = extract_canonical_from_html(html, base_url="https://example.com/artigo")
    assert result is None


def test_extract_canonical_from_html_returns_none_when_absent():
    html = "<html><head></head><body>sem canonical</body></html>"
    result = extract_canonical_from_html(html, base_url="https://example.com/artigo")
    assert result is None
