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
