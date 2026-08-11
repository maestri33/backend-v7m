"""Testes diretos de `finance.interface.fees` — a saída de DINHEIRO (taxa do credenciador) que o funil
de matrícula enfileira. O explorador de arquitetura apontou que a costura enrollment↔finance é uma
CONVENÇÃO DE STRING (prefixo de referência) sem teste: `latest_fee_request`/`retry_fee_payment` casam
por prefixo, e renomear o prefixo desanexaria taxas pagas em silêncio. Aqui a régua fica travada ANTES
de qualquer refactor (candidato #9) — e o parse de data (`_due_to_scheduled`) é do tipo que já teve bug
de timezone no _analysis, então vale checar.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from django.utils import timezone

from finance.interface import fees
from finance.models import PaymentRequest

pytestmark = pytest.mark.django_db


def _fee(ref: str, *, amount="10.00", status=None, kind=None, source_id=None):
    return PaymentRequest.objects.create(
        external_reference=ref,
        kind=kind or PaymentRequest.Kind.FEE,
        method=PaymentRequest.Method.PIX_QRCODE,
        amount=Decimal(amount),
        status=status or PaymentRequest.Status.QUEUED,
        source_type=fees.SourceType.ENROLLMENT,
        source_external_id=source_id or uuid.uuid4(),
    )


# ── request_fee_payment: idempotência por referência + campos ──


def test_request_fee_payment_campos_e_quantize():
    pr = fees.request_fee_payment(
        amount="5.5", qr_payload="qr-copia-cola", external_reference="fee_camp_now"
    )
    assert pr.amount == Decimal("5.50")  # quantizado 2 casas
    assert pr.kind == PaymentRequest.Kind.FEE
    assert pr.status == PaymentRequest.Status.QUEUED
    assert pr.method == PaymentRequest.Method.PIX_QRCODE
    assert pr.next_attempt_at is not None  # imediato → agora


def test_request_fee_payment_idempotente_por_referencia():
    pr1 = fees.request_fee_payment(
        amount=10, qr_payload="qr", external_reference="fee_idem_now"
    )
    # mesma ref, valor diferente → devolve a MESMA linha, NÃO sobrescreve nem duplica
    pr2 = fees.request_fee_payment(
        amount=999, qr_payload="qr2", external_reference="fee_idem_now"
    )
    assert pr1.pk == pr2.pk
    assert pr2.amount == Decimal("10.00")
    assert PaymentRequest.objects.filter(external_reference="fee_idem_now").count() == 1


# ── latest_fee_request: casa por PREFIXO + só FEE + mais recente ──


def test_latest_fee_request_ignora_commission_no_mesmo_prefixo():
    _fee("fee_enr_ABC_now", kind=PaymentRequest.Kind.FEE)
    # uma COMMISSION cujo external_reference colide no prefixo NÃO pode ser pega
    _fee("fee_enr_ABC_now_commission", kind=PaymentRequest.Kind.COMMISSION)
    got = fees.latest_fee_request("fee_enr_ABC_now")
    assert got is not None
    assert got.kind == PaymentRequest.Kind.FEE


def test_latest_fee_request_pega_a_mais_recente_da_familia():
    old = _fee("fee_enr_XYZ_now")
    new = _fee("fee_enr_XYZ_now_r2")
    # created_at é auto_now_add → força ordem determinística via update (bypassa auto_now)
    past = timezone.now() - timezone.timedelta(minutes=5)
    PaymentRequest.objects.filter(pk=old.pk).update(created_at=past)
    got = fees.latest_fee_request("fee_enr_XYZ_now")
    assert got.pk == new.pk


def test_latest_fee_request_none_quando_nao_ha():
    assert fees.latest_fee_request("fee_inexistente") is None


# ── retry_fee_payment: só após FALHA, referência fresca `_rN`, carrega a origem ──


def test_retry_fee_payment_bloqueia_se_nao_falhou():
    _fee("fee_enr_Q_now", status=PaymentRequest.Status.QUEUED)
    with pytest.raises(ValueError, match="não está em falha"):
        fees.retry_fee_payment("fee_enr_Q_now", qr_payload="qr", amount=10)


def test_retry_fee_payment_inexistente_levanta():
    with pytest.raises(ValueError, match="inexistente"):
        fees.retry_fee_payment("fee_nada", qr_payload="qr", amount=10)


def test_retry_fee_payment_cria_rN_e_carrega_origem():
    sid = uuid.uuid4()
    failed = _fee("fee_enr_R_now", status=PaymentRequest.Status.FAILED, source_id=sid)
    retry = fees.retry_fee_payment("fee_enr_R_now", qr_payload="qr-novo", amount=10)
    # 1 tentativa existente → count 1 + 1 = 2 → sufixo _r2
    assert retry.external_reference == "fee_enr_R_now_r2"
    # a re-tentativa herda a relação com a matrícula de origem (senão o webhook não pareia)
    assert retry.source_type == failed.source_type
    assert retry.source_external_id == sid


# ── _due_to_scheduled: dueDate do Asaas → 09:00 no fuso de SP ──


def test_due_to_scheduled_data_pura_vira_9h_sp():
    got = fees._due_to_scheduled("2026-06-15")
    assert got.hour == 9
    assert str(got.tzinfo) == "America/Sao_Paulo"
    assert got.date().isoformat() == "2026-06-15"


def test_due_to_scheduled_iso_com_fuso_passa_direto():
    from datetime import datetime

    got = fees._due_to_scheduled("2026-06-15T14:30:00+00:00")
    assert got == datetime.fromisoformat("2026-06-15T14:30:00+00:00")


def test_due_to_scheduled_lixo_levanta_valueerror():
    with pytest.raises(ValueError, match="formato inesperado"):
        fees._due_to_scheduled("não-é-data")
