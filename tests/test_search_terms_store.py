from __future__ import annotations

from pathlib import Path

import pytest

from search_terms_store import (
    DEFAULT_RETRIEVAL_TYPE,
    SearchTermValidationError,
    load_and_validate,
    sync_search_terms,
)

VALID_YAML = """
search_terms:
  - id: termo-a
    query: "consulta A"
    category: politician
    entity: entidade-a
    enabled: true
  - id: termo-b
    query: "consulta B"
    category: topic
    entity: entidade-b
    topic: economy
    retrieval_type: opinion_query
    enabled: false
"""


def _write_yaml(tmp_path: Path, content: str, name: str = "search_terms.yaml") -> Path:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


def test_load_and_validate_valid_yaml(tmp_path):
    path = _write_yaml(tmp_path, VALID_YAML)
    terms = load_and_validate(path)

    assert [t.id for t in terms] == ["termo-a", "termo-b"]

    term_a = terms[0]
    assert term_a.query == "consulta A"
    assert term_a.category == "politician"
    assert term_a.enabled is True
    assert term_a.retrieval_type == DEFAULT_RETRIEVAL_TYPE  # ausente no YAML -> default 'neutral'

    term_b = terms[1]
    assert term_b.retrieval_type == "opinion_query"
    assert term_b.enabled is False


def test_load_and_validate_rejects_missing_required_field(tmp_path):
    invalid_yaml = """
search_terms:
  - id: termo-sem-query
    category: politician
    enabled: true
"""
    path = _write_yaml(tmp_path, invalid_yaml)

    with pytest.raises(SearchTermValidationError) as exc_info:
        load_and_validate(path)

    message = str(exc_info.value)
    assert "termo-sem-query" in message
    assert "query" in message


def test_load_and_validate_rejects_invalid_retrieval_type(tmp_path):
    invalid_yaml = """
search_terms:
  - id: termo-x
    query: "algo"
    category: politician
    retrieval_type: tipo_que_nao_existe
    enabled: true
"""
    path = _write_yaml(tmp_path, invalid_yaml)

    with pytest.raises(SearchTermValidationError) as exc_info:
        load_and_validate(path)

    message = str(exc_info.value)
    assert "termo-x" in message
    assert "retrieval_type" in message


def test_load_and_validate_rejects_duplicate_id(tmp_path):
    dup_yaml = """
search_terms:
  - id: repetido
    query: "a"
    category: politician
    enabled: true
  - id: repetido
    query: "b"
    category: politician
    enabled: true
"""
    path = _write_yaml(tmp_path, dup_yaml)

    with pytest.raises(SearchTermValidationError) as exc_info:
        load_and_validate(path)

    message = str(exc_info.value)
    assert "repetido" in message
    assert "duplicado" in message


def test_only_enabled_terms_are_selected_for_execution(tmp_path):
    path = _write_yaml(tmp_path, VALID_YAML)
    terms = load_and_validate(path)
    enabled = [t for t in terms if t.enabled]
    assert [t.id for t in enabled] == ["termo-a"]


def test_sync_search_terms_is_idempotent(tmp_path, db_conn):
    path = _write_yaml(tmp_path, VALID_YAML)
    terms = load_and_validate(path)

    first = sync_search_terms(db_conn, terms)
    db_conn.commit()
    assert first.inserted == 2
    assert first.updated == 0

    second = sync_search_terms(db_conn, terms)
    db_conn.commit()
    assert second.inserted == 0
    assert second.updated == 2

    with db_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM search_terms")
        assert cur.fetchone()[0] == 2


def test_sync_search_terms_does_not_delete_terms_missing_from_yaml(tmp_path, db_conn):
    path = _write_yaml(tmp_path, VALID_YAML, name="full.yaml")
    terms = load_and_validate(path)
    sync_search_terms(db_conn, terms)
    db_conn.commit()

    reduced_yaml = """
search_terms:
  - id: termo-a
    query: "consulta A atualizada"
    category: politician
    entity: entidade-a
    enabled: true
"""
    reduced_path = _write_yaml(tmp_path, reduced_yaml, name="reduced.yaml")
    reduced_terms = load_and_validate(reduced_path)
    sync_search_terms(db_conn, reduced_terms)
    db_conn.commit()

    with db_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM search_terms")
        assert cur.fetchone()[0] == 2  # termo-b continua lá, só não foi tocado

        cur.execute("SELECT query FROM search_terms WHERE id = 'termo-a'")
        assert cur.fetchone()[0] == "consulta A atualizada"
