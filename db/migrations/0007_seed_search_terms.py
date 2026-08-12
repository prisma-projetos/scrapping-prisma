"""Migration Python (popular search_terms a partir do YAML é inerentemente
uma operação de aplicação, não dá pra fazer em SQL puro).

Não chama conn.commit()/rollback() — o runner (src/storage.py) comita esta
migration inteira junto com o registro em schema_migrations, atomicamente.
"""

from __future__ import annotations

from pathlib import Path

from search_terms_store import load_and_validate, sync_search_terms

SEARCH_TERMS_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "search_terms.yaml"


def migrate(conn) -> None:
    terms = load_and_validate(SEARCH_TERMS_PATH)
    sync_search_terms(conn, terms)

    # Fallback defensivo: qualquer search_term_id usado em articles que não
    # exista em search_terms (não deve acontecer hoje — os 2 IDs legados,
    # reforma-tributaria e eleicoes-2026, existem no YAML atual — mas
    # protege o caso geral contra perda silenciosa de dados).
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO search_terms (id, query, category, enabled)
            SELECT DISTINCT a.search_term_id,
                   '[MIGRAÇÃO: termo legado, consulta original não registrada]',
                   'legacy_migration',
                   false
            FROM articles a
            WHERE a.search_term_id IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM search_terms st WHERE st.id = a.search_term_id
              )
            ON CONFLICT (id) DO NOTHING
            """
        )
