"""Carrega e valida a lista de entidades-alvo (candidatos e partidos vinculados)
exportada pelo `classifier-prisma`.

Formato confirmado com o time do Classifier (2026-08-29): geração **sob
demanda**, não agendada — a lista de candidatos a presidente/vice muda por
evento raro e conhecido (substituição, impugnação, desistência), não por
tempo. O Classifier gera um novo export e avisa quando a lista mudar de
verdade; não há necessidade de um job periódico neste repositório para
manter isso em dia.

`entity_id` é a mesma chave usada em `app_db2`
(`nr_titulo_eleitoral_candidato` para políticos, `nr_partido` para
partidos) — permite cruzar dado com o Classifier sem duplicar lógica de
match. Sempre normalizado para `str` na leitura: alguns desses números vêm
sem aspas no YAML e, dependendo dos dígitos, o parser pode interpretar
como inteiro (perdendo zero à esquerda) — não dá para confiar no dado de
origem estar sempre entre aspas.

Este módulo é só leitura: não sincroniza nada em banco de dados (sem
`psycopg`, sem `storage.py`) e ainda não é chamado de `src/main.py` — a
decisão de como/quando integrar isso no pipeline de coleta (faseamento) é
separada.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml

VALID_ENTITY_TYPES = {"partido", "politico"}
VALID_CARGOS = {"PRESIDENTE", "VICE-PRESIDENTE"}


@dataclass(frozen=True)
class TargetEntity:
    entity_id: str
    entity_type: str  # partido | politico
    cargo: str | None  # PRESIDENTE | VICE-PRESIDENTE | None (None só para partido)
    nome_principal: str
    aliases: list[str]


@dataclass(frozen=True)
class TargetEntitiesExport:
    generated_at: str
    schema_version: int
    source: str
    entities: list[TargetEntity]


class TargetEntitiesValidationError(Exception):
    """Agrega todos os problemas encontrados no YAML, não só o primeiro."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("\n".join(errors))


def _normalize_entity_id(raw_id: object) -> str | None:
    if isinstance(raw_id, int):
        return str(raw_id)
    if isinstance(raw_id, str) and raw_id.strip():
        return raw_id.strip()
    return None


def _validate_entry(index: int, raw: dict) -> tuple[TargetEntity | None, list[str]]:
    errors: list[str] = []
    label = f"entidade #{index}"

    entity_id = _normalize_entity_id(raw.get("entity_id"))
    if entity_id is None:
        errors.append(f"{label}: campo 'entity_id' ausente ou vazio")
    else:
        label = f"entidade '{entity_id}'"

    raw_type = raw.get("entity_type")
    if not isinstance(raw_type, str) or not raw_type.strip():
        errors.append(f"{label}: campo 'entity_type' ausente ou vazio")
        raw_type = None
    elif raw_type not in VALID_ENTITY_TYPES:
        errors.append(
            f"{label}: campo 'entity_type' com valor inválido {raw_type!r} "
            f"(esperado um de {sorted(VALID_ENTITY_TYPES)})"
        )
        raw_type = None

    cargo = raw.get("cargo")
    if cargo is not None and not isinstance(cargo, str):
        errors.append(f"{label}: campo 'cargo' deve ser texto ou nulo")
        cargo = None
    elif isinstance(cargo, str) and cargo not in VALID_CARGOS:
        errors.append(
            f"{label}: campo 'cargo' com valor inválido {cargo!r} (esperado um de {sorted(VALID_CARGOS)} ou nulo)"
        )
        cargo = None

    if raw_type == "partido" and cargo is not None:
        errors.append(f"{label}: entity_type 'partido' não deveria ter 'cargo' preenchido (veio {cargo!r})")
    if raw_type == "politico" and cargo is None:
        errors.append(f"{label}: entity_type 'politico' precisa de 'cargo' preenchido ({sorted(VALID_CARGOS)})")

    raw_nome = raw.get("nome_principal")
    if not isinstance(raw_nome, str) or not raw_nome.strip():
        errors.append(f"{label}: campo 'nome_principal' ausente ou vazio")

    raw_aliases = raw.get("aliases")
    aliases: list[str] = []
    if not isinstance(raw_aliases, list) or not raw_aliases:
        errors.append(f"{label}: campo 'aliases' deve ser uma lista não vazia")
    elif not all(isinstance(a, str) and a.strip() for a in raw_aliases):
        errors.append(f"{label}: campo 'aliases' deve conter só textos não vazios")
    else:
        aliases = [a.strip() for a in raw_aliases]

    if errors:
        return None, errors

    return (
        TargetEntity(
            entity_id=entity_id,
            entity_type=raw_type,
            cargo=cargo,
            nome_principal=raw_nome.strip(),
            aliases=aliases,
        ),
        [],
    )


def load_target_entities_export(path: Path) -> TargetEntitiesExport:
    """Lê e valida o export completo (metadados + entidades).

    Só leitura de arquivo — não toca em banco de dados. Levanta
    `TargetEntitiesValidationError` com todos os problemas encontrados de
    uma vez (não só o primeiro), no mesmo padrão de
    `search_terms_store.load_and_validate`.
    """
    with open(path, encoding="utf-8") as f:
        raw_doc = yaml.safe_load(f)

    if not isinstance(raw_doc, dict):
        raise TargetEntitiesValidationError([f"{path}: documento deve ser um mapeamento de campos"])

    all_errors: list[str] = []

    generated_at = raw_doc.get("generated_at")
    if not isinstance(generated_at, str) or not generated_at.strip():
        all_errors.append(f"{path}: campo 'generated_at' ausente ou vazio")

    schema_version = raw_doc.get("schema_version")
    if not isinstance(schema_version, int):
        all_errors.append(f"{path}: campo 'schema_version' ausente ou não é inteiro")

    source = raw_doc.get("source")
    if not isinstance(source, str) or not source.strip():
        all_errors.append(f"{path}: campo 'source' ausente ou vazio")

    raw_entities = raw_doc.get("entities")
    if not isinstance(raw_entities, list):
        all_errors.append(f"{path}: 'entities' ausente ou não é uma lista")
        raw_entities = []

    entities: list[TargetEntity] = []
    seen_ids: dict[str, int] = {}

    for index, raw in enumerate(raw_entities, start=1):
        if not isinstance(raw, dict):
            all_errors.append(f"entidade #{index}: entrada deve ser um mapeamento de campos")
            continue

        entity, errors = _validate_entry(index, raw)
        if errors:
            all_errors.extend(errors)
            continue

        if entity.entity_id in seen_ids:
            all_errors.append(
                f"entidade '{entity.entity_id}' (#{index}): id duplicado, "
                f"já usado na entidade #{seen_ids[entity.entity_id]}"
            )
            continue

        seen_ids[entity.entity_id] = index
        entities.append(entity)

    if all_errors:
        raise TargetEntitiesValidationError(all_errors)

    return TargetEntitiesExport(
        generated_at=generated_at,
        schema_version=schema_version,
        source=source,
        entities=entities,
    )


def load_target_entities(path: Path) -> list[TargetEntity]:
    """Atalho para quem só precisa da lista de entidades, sem os metadados do export."""
    return load_target_entities_export(path).entities


def check_freshness(generated_at: str, max_age_days: int = 30) -> str | None:
    """Alerta informativo se o export estiver mais velho que `max_age_days`.

    Não lança exceção e não trava a leitura do arquivo — o export continua
    utilizável mesmo se estiver velho, isso é só um sinal para logar. Como a
    geração é sob demanda (não agendada), um export "velho" não é
    necessariamente um problema — é só um sinal para checar se a lista de
    candidatos ainda está atualizada.
    """
    try:
        normalized = generated_at.strip()
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"
        parsed = datetime.fromisoformat(normalized)
    except (ValueError, AttributeError):
        return (
            f"generated_at {generated_at!r} não é uma data ISO 8601 reconhecível — "
            "não foi possível checar a idade do export"
        )

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    age_days = (datetime.now(timezone.utc) - parsed).days
    if age_days > max_age_days:
        return (
            f"export de entidades-alvo gerado em {generated_at} tem {age_days} dia(s) "
            f"(limite recomendado: {max_age_days} dias) — pode estar desatualizado"
        )

    return None
