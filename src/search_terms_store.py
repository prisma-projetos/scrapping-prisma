from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import psycopg
import yaml

VALID_RETRIEVAL_TYPES = {"neutral", "opinion_query", "entity_topic", "event"}
DEFAULT_RETRIEVAL_TYPE = "neutral"


@dataclass(frozen=True)
class SearchTerm:
    id: str
    query: str
    category: str
    entity: str | None
    topic: str | None
    retrieval_type: str
    enabled: bool


@dataclass(frozen=True)
class SyncResult:
    inserted: int
    updated: int


class SearchTermValidationError(Exception):
    """Agrega todos os problemas encontrados no YAML, não só o primeiro."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("\n".join(errors))


def _validate_entry(index: int, raw: dict) -> tuple[SearchTerm | None, list[str]]:
    errors: list[str] = []
    label = f"termo #{index}"

    raw_id = raw.get("id")
    term_id = None
    if not isinstance(raw_id, str) or not raw_id.strip():
        errors.append(f"{label}: campo 'id' ausente ou vazio")
    else:
        term_id = raw_id.strip()
        label = f"termo '{term_id}'"

    raw_query = raw.get("query")
    if not isinstance(raw_query, str) or not raw_query.strip():
        errors.append(f"{label}: campo 'query' ausente ou vazio")

    raw_category = raw.get("category")
    if not isinstance(raw_category, str) or not raw_category.strip():
        errors.append(f"{label}: campo 'category' ausente ou vazio")

    entity = raw.get("entity")
    if entity is not None and not isinstance(entity, str):
        errors.append(f"{label}: campo 'entity' deve ser texto ou nulo")

    topic = raw.get("topic")
    if topic is not None and not isinstance(topic, str):
        errors.append(f"{label}: campo 'topic' deve ser texto ou nulo")

    retrieval_type = raw.get("retrieval_type")
    if retrieval_type is None:
        retrieval_type = DEFAULT_RETRIEVAL_TYPE
    elif retrieval_type not in VALID_RETRIEVAL_TYPES:
        errors.append(
            f"{label}: campo 'retrieval_type' com valor inválido {raw.get('retrieval_type')!r} "
            f"(esperado um de {sorted(VALID_RETRIEVAL_TYPES)})"
        )

    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        errors.append(f"{label}: campo 'enabled' deve ser verdadeiro/falso")

    if errors:
        return None, errors

    return (
        SearchTerm(
            id=term_id,
            query=raw_query.strip(),
            category=raw_category.strip(),
            entity=entity,
            topic=topic,
            retrieval_type=retrieval_type,
            enabled=enabled,
        ),
        [],
    )


def load_and_validate(path: Path) -> list[SearchTerm]:
    with open(path, encoding="utf-8") as f:
        raw_doc = yaml.safe_load(f)

    if not isinstance(raw_doc, dict) or "search_terms" not in raw_doc:
        raise SearchTermValidationError([f"{path}: chave raiz 'search_terms' ausente"])

    raw_terms = raw_doc["search_terms"]
    if not isinstance(raw_terms, list):
        raise SearchTermValidationError([f"{path}: 'search_terms' deve ser uma lista"])

    terms: list[SearchTerm] = []
    all_errors: list[str] = []
    seen_ids: dict[str, int] = {}

    for index, raw in enumerate(raw_terms, start=1):
        if not isinstance(raw, dict):
            all_errors.append(f"termo #{index}: entrada deve ser um mapeamento de campos")
            continue

        term, errors = _validate_entry(index, raw)
        if errors:
            all_errors.extend(errors)
            continue

        if term.id in seen_ids:
            all_errors.append(f"termo '{term.id}' (#{index}): id duplicado, já usado no termo #{seen_ids[term.id]}")
            continue

        seen_ids[term.id] = index
        terms.append(term)

    if all_errors:
        raise SearchTermValidationError(all_errors)

    return terms


def sync_search_terms(conn: psycopg.Connection, terms: list[SearchTerm]) -> SyncResult:
    """Upsert idempotente. Nunca apaga termos ausentes do YAML — só não os toca.

    Não comita a transação: quem chama decide quando (permite reuso tanto
    no fluxo normal do main.py quanto dentro de uma migration .py, onde o
    runner precisa comitar migration + registro em schema_migrations
    juntos, atomicamente)."""
    inserted = 0
    updated = 0

    with conn.cursor() as cur:
        for term in terms:
            cur.execute(
                """
                INSERT INTO search_terms (id, query, category, entity, topic, retrieval_type, enabled)
                VALUES (%(id)s, %(query)s, %(category)s, %(entity)s, %(topic)s, %(retrieval_type)s, %(enabled)s)
                ON CONFLICT (id) DO UPDATE SET
                    query = EXCLUDED.query,
                    category = EXCLUDED.category,
                    entity = EXCLUDED.entity,
                    topic = EXCLUDED.topic,
                    retrieval_type = EXCLUDED.retrieval_type,
                    enabled = EXCLUDED.enabled,
                    updated_at = now()
                RETURNING (xmax = 0) AS was_insert
                """,
                term.__dict__,
            )
            row = cur.fetchone()
            if row[0]:
                inserted += 1
            else:
                updated += 1

    return SyncResult(inserted=inserted, updated=updated)
