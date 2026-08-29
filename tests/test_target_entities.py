from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from target_entities import (
    TargetEntitiesValidationError,
    check_freshness,
    load_target_entities,
    load_target_entities_export,
)

VALID_YAML = """
generated_at: "2026-08-29T00:00:00Z"
schema_version: 1
source: "classifier-prisma/entidade_matching (TSE 2026, escopo v1)"
entities:
  - entity_id: '13'
    entity_type: partido
    cargo: null
    nome_principal: PARTIDO DOS TRABALHADORES
    aliases:
      - PT
      - PARTIDO DOS TRABALHADORES
  - entity_id: '122418060191'
    entity_type: politico
    cargo: PRESIDENTE
    nome_principal: LUIZ INÁCIO LULA DA SILVA
    aliases:
      - LUIZ INÁCIO LULA DA SILVA
      - LULA
"""


def _write_yaml(tmp_path: Path, content: str, name: str = "target_entities.yaml") -> Path:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


def test_load_target_entities_valid_yaml(tmp_path):
    path = _write_yaml(tmp_path, VALID_YAML)
    entities = load_target_entities(path)

    assert [e.entity_id for e in entities] == ["13", "122418060191"]

    partido = entities[0]
    assert partido.entity_type == "partido"
    assert partido.cargo is None
    assert partido.aliases == ["PT", "PARTIDO DOS TRABALHADORES"]

    politico = entities[1]
    assert politico.entity_type == "politico"
    assert politico.cargo == "PRESIDENTE"
    assert politico.nome_principal == "LUIZ INÁCIO LULA DA SILVA"


def test_load_target_entities_export_exposes_metadata(tmp_path):
    path = _write_yaml(tmp_path, VALID_YAML)
    export = load_target_entities_export(path)

    assert export.generated_at == "2026-08-29T00:00:00Z"
    assert export.schema_version == 1
    assert "classifier-prisma" in export.source
    assert len(export.entities) == 2


def test_load_target_entities_normalizes_unquoted_numeric_id(tmp_path):
    """entity_id sem aspas com dígitos 1-9 iniciais é lido como int pelo YAML —
    o loader precisa normalizar pra str, senão perde comparabilidade com o
    resto do código (que trata entity_id como string)."""
    yaml_with_bare_int_id = """
generated_at: "2026-08-29T00:00:00Z"
schema_version: 1
source: "teste"
entities:
  - entity_id: 108563260302
    entity_type: politico
    cargo: PRESIDENTE
    nome_principal: FLAVIO NANTES BOLSONARO
    aliases:
      - FLAVIO BOLSONARO
"""
    path = _write_yaml(tmp_path, yaml_with_bare_int_id)
    entities = load_target_entities(path)

    assert entities[0].entity_id == "108563260302"
    assert isinstance(entities[0].entity_id, str)


def test_load_target_entities_rejects_duplicate_id(tmp_path):
    dup_yaml = """
generated_at: "2026-08-29T00:00:00Z"
schema_version: 1
source: "teste"
entities:
  - entity_id: 'repetido'
    entity_type: politico
    cargo: PRESIDENTE
    nome_principal: "Alguém"
    aliases: ["Alguém"]
  - entity_id: 'repetido'
    entity_type: politico
    cargo: VICE-PRESIDENTE
    nome_principal: "Outro Alguém"
    aliases: ["Outro Alguém"]
"""
    path = _write_yaml(tmp_path, dup_yaml)

    with pytest.raises(TargetEntitiesValidationError) as exc_info:
        load_target_entities(path)

    message = str(exc_info.value)
    assert "repetido" in message
    assert "duplicado" in message


def test_load_target_entities_rejects_invalid_entity_type(tmp_path):
    invalid_yaml = """
generated_at: "2026-08-29T00:00:00Z"
schema_version: 1
source: "teste"
entities:
  - entity_id: 'fulano'
    entity_type: tipo_que_nao_existe
    cargo: null
    nome_principal: "Fulano de Tal"
    aliases: ["Fulano"]
"""
    path = _write_yaml(tmp_path, invalid_yaml)

    with pytest.raises(TargetEntitiesValidationError) as exc_info:
        load_target_entities(path)

    message = str(exc_info.value)
    assert "fulano" in message
    assert "entity_type" in message


def test_load_target_entities_rejects_partido_with_cargo(tmp_path):
    invalid_yaml = """
generated_at: "2026-08-29T00:00:00Z"
schema_version: 1
source: "teste"
entities:
  - entity_id: '99'
    entity_type: partido
    cargo: PRESIDENTE
    nome_principal: "Partido Fictício"
    aliases: ["PF"]
"""
    path = _write_yaml(tmp_path, invalid_yaml)

    with pytest.raises(TargetEntitiesValidationError) as exc_info:
        load_target_entities(path)

    assert "cargo" in str(exc_info.value)


def test_load_target_entities_rejects_politico_without_cargo(tmp_path):
    invalid_yaml = """
generated_at: "2026-08-29T00:00:00Z"
schema_version: 1
source: "teste"
entities:
  - entity_id: '123'
    entity_type: politico
    cargo: null
    nome_principal: "Fulano de Tal"
    aliases: ["Fulano"]
"""
    path = _write_yaml(tmp_path, invalid_yaml)

    with pytest.raises(TargetEntitiesValidationError) as exc_info:
        load_target_entities(path)

    assert "cargo" in str(exc_info.value)


def test_load_target_entities_rejects_empty_aliases(tmp_path):
    invalid_yaml = """
generated_at: "2026-08-29T00:00:00Z"
schema_version: 1
source: "teste"
entities:
  - entity_id: '123'
    entity_type: politico
    cargo: PRESIDENTE
    nome_principal: "Fulano de Tal"
    aliases: []
"""
    path = _write_yaml(tmp_path, invalid_yaml)

    with pytest.raises(TargetEntitiesValidationError) as exc_info:
        load_target_entities(path)

    assert "aliases" in str(exc_info.value)


def test_load_target_entities_rejects_missing_required_field(tmp_path):
    invalid_yaml = """
generated_at: "2026-08-29T00:00:00Z"
schema_version: 1
source: "teste"
entities:
  - entity_id: 'sem-nome'
    entity_type: politico
    cargo: PRESIDENTE
    aliases: ["Sem Nome"]
"""
    path = _write_yaml(tmp_path, invalid_yaml)

    with pytest.raises(TargetEntitiesValidationError) as exc_info:
        load_target_entities(path)

    message = str(exc_info.value)
    assert "sem-nome" in message
    assert "nome_principal" in message


def test_check_freshness_returns_none_for_recent_date():
    recent = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat().replace("+00:00", "Z")
    assert check_freshness(recent, max_age_days=30) is None


def test_check_freshness_returns_warning_for_old_date():
    old = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat().replace("+00:00", "Z")
    warning = check_freshness(old, max_age_days=30)

    assert warning is not None
    assert "desatualizado" in warning
