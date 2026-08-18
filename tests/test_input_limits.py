"""Limites de tamanho na borda (auditoria API B3): sem constraint Pydantic, uma string longa
passava a borda e estourava DataError (varchar) no Postgres → 500 (falha do servidor no Sentry)
em vez de 422 de entrada. O bug é invisível em SQLite (não trunca), então testamos o SCHEMA
diretamente: a validação tem que rejeitar ANTES de chegar no banco."""

import pytest
from pydantic import ValidationError as PydanticValidationError


def test_address_street_longa_e_rejeitada_no_schema():
    from api.schemas import AddressDataIn

    with pytest.raises(PydanticValidationError):
        AddressDataIn(street="A" * 300)  # coluna é varchar(200)


def test_address_valida_passa():
    from api.schemas import AddressDataIn

    ok = AddressDataIn(street="Rua das Flores", number="123", city="Ponta Grossa")
    assert ok.street == "Rua das Flores"


def test_kinship_relation_longa_rejeitada():
    from api.clients import KinshipIn

    with pytest.raises(PydanticValidationError):
        KinshipIn(relation="X" * 300)  # coluna é varchar(200)


def test_rg_number_longo_rejeitado():
    from api.clients import RgPatchIn

    with pytest.raises(PydanticValidationError):
        RgPatchIn(number="9" * 40)  # coluna é varchar(30)


def test_rg_patch_valido_passa():
    from api.clients import RgPatchIn

    ok = RgPatchIn(number="12.345.678-9", mother_name="Maria da Silva")
    assert ok.number == "12.345.678-9"


@pytest.mark.parametrize(
    "field,col_max",
    [("birthplace", 128), ("marital_status", 32), ("nationality", 64)],
)
def test_rg_patch_campos_de_profile_tambem_limitados(field, col_max):
    """Os 3 campos que iam pro Profile (varchar 128/32/64) e escaparam do 1º fix — string longa
    tem que ser rejeitada no schema, não estourar DataError no banco (500)."""
    from api.clients import RgPatchIn

    with pytest.raises(PydanticValidationError):
        RgPatchIn(**{field: "X" * (col_max + 50)})


@pytest.mark.parametrize(
    "schema_path,field,col_max",
    [
        ("api.collaborators.ProfileIn", "birthplace", 128),
        ("api.collaborators.ProfileIn", "marital_status", 32),
        ("api.collaborators.DocumentsIn", "national_register", 30),
        ("api.collaborators.KinshipIn", "relation", 200),
    ],
)
def test_funil_candidate_schemas_limitados(schema_path, field, col_max):
    """O funil candidate (ProfileIn/DocumentsIn/KinshipIn) também precisa dos limites — mesmo 500."""
    import importlib

    mod_name, cls_name = schema_path.rsplit(".", 1)
    cls = getattr(importlib.import_module(mod_name), cls_name)
    kwargs = {field: "X" * (col_max + 50)}
    # DocumentsIn/KinshipIn têm campos obrigatórios; preenche os mínimos
    if cls_name == "DocumentsIn":
        kwargs.setdefault("doc_type", "rg")
        kwargs.setdefault("number", "123")
    if cls_name == "KinshipIn" and field != "relation":
        kwargs.setdefault("relation", "mãe")
    with pytest.raises(PydanticValidationError):
        cls(**kwargs)
