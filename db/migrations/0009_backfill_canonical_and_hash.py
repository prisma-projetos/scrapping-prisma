"""Migration Python: recalcula canonical_link e content_hash para as
linhas legadas já existentes, reaproveitando as mesmas funções usadas em
runtime (evita divergência entre o hash/canonical legado e o novo). Não
faz nenhuma requisição de rede — deriva tudo de resolved_link/full_text
já armazenados. Não toca em extraction_status, então o trigger de
enfileiramento (escopado a mudanças nessa coluna) não é disparado por
este backfill.
"""

from __future__ import annotations

from collections import defaultdict

from articles_store import compute_content_hash
from url_utils import normalize_url


def migrate(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT id, resolved_link, full_text FROM articles WHERE resolved_link IS NOT NULL")
        rows = cur.fetchall()

    canonical_by_id: dict[int, str] = {}
    ids_by_canonical: dict[str, list[int]] = defaultdict(list)

    for article_id, resolved_link, _full_text in rows:
        canonical = normalize_url(resolved_link)
        canonical_by_id[article_id] = canonical
        ids_by_canonical[canonical].append(article_id)

    collisions = {canonical: ids for canonical, ids in ids_by_canonical.items() if len(ids) > 1}
    if collisions:
        details = "; ".join(f"{canonical!r} -> ids {ids}" for canonical, ids in collisions.items())
        raise RuntimeError(
            "Migração abortada: canonical_link colidiu entre artigos distintos ao normalizar dados legados: "
            + details
        )

    with conn.cursor() as cur:
        for article_id, resolved_link, full_text in rows:
            content_hash = compute_content_hash(full_text) if full_text else None
            cur.execute(
                "UPDATE articles SET canonical_link = %s, content_hash = %s WHERE id = %s",
                (canonical_by_id[article_id], content_hash, article_id),
            )
