"""Testes da taxa do credenciador no funil de matrícula (`enrollment.service`) — o gate de DINHEIRO que
decide a promoção matrícula→aluno. O explorador apontou que `conclude` (trava a conclusão em
`1ª paga ∧ 2ª agendada`) e os hooks `apply_fee_paid`/`apply_fee_problem` não tinham teste — o e2e
seedava a fila na mão. Aqui trava-se a REGRA (não promover sem as 2 parcelas; casar por PREFIXO mesmo
com re-tentativa `_rN`) sem depender do caminho pesado de promoção.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from finance.models import PaymentRequest
from users.exceptions import Conflict
from users.roles.enrollment import service as es
from users.roles.enrollment.models import Enrollment

pytestmark = pytest.mark.django_db

_S = Enrollment.Status


def _enrollment(status):
    from hub.models import Hub
    from users.address.models import Address
    from users.auth.models import User
    from users.profiles.models import Profile

    coord = User.objects.create_user(external_id=uuid.uuid4())
    Profile.objects.create(
        user=coord, cpf=str(uuid.uuid4().int)[:11], phone=str(uuid.uuid4().int)[:13]
    )
    addr = Address.objects.create(city="São Paulo", state="SP")
    hub = Hub.objects.create(
        address=addr, brand="test", coordinator=coord, is_default=False
    )
    user = User.objects.create_user(external_id=uuid.uuid4())
    promoter = User.objects.create_user(external_id=uuid.uuid4())
    enr = Enrollment.objects.create(
        user=user, promoter=promoter, hub=hub, status=status
    )
    return enr, coord


def _seed_fee(enr, suffix, status):
    return PaymentRequest.objects.create(
        external_reference=f"fee_enr_{enr.external_id}_{suffix}",
        kind=PaymentRequest.Kind.FEE,
        method=PaymentRequest.Method.PIX_QRCODE,
        amount=Decimal("100.00"),
        status=status,
        source_type="enrollment",
        source_external_id=enr.external_id,
    )


# ── conclude: NÃO promove sem as 2 parcelas (1ª paga ∧ 2ª agendada) ──


def _conclude(enr, coord):
    return es.conclude(
        enrollment_external_id=str(enr.external_id),
        coordinator=coord,
        platform_login="aluno@plat",
        platform_password="segredo",
    )


def test_conclude_bloqueia_sem_nenhuma_parcela():
    enr, coord = _enrollment(_S.AWAITING_RELEASE)
    with pytest.raises(Conflict) as exc:
        _conclude(enr, coord)
    assert exc.value.code == "FEES_INCOMPLETE"
    assert set(exc.value.extra["missing"]) == {"first_fee_paid", "second_fee_scheduled"}
    enr.refresh_from_db()
    assert enr.status == _S.AWAITING_RELEASE  # NÃO promoveu


def test_conclude_bloqueia_so_com_a_primeira_paga():
    enr, coord = _enrollment(_S.FEE_PAID)
    _seed_fee(enr, "now", PaymentRequest.Status.PAID)  # 1ª paga, 2ª ausente
    with pytest.raises(Conflict) as exc:
        _conclude(enr, coord)
    assert exc.value.extra["missing"] == ["second_fee_scheduled"]


def test_conclude_bloqueia_so_com_a_segunda_agendada():
    enr, coord = _enrollment(_S.FEE_SCHEDULED)
    _seed_fee(enr, "due", PaymentRequest.Status.QUEUED)  # 2ª existe, 1ª não paga
    with pytest.raises(Conflict) as exc:
        _conclude(enr, coord)
    assert exc.value.extra["missing"] == ["first_fee_paid"]


def test_conclude_primeira_so_agendada_nao_basta():
    # 1ª parcela existe mas NÃO está PAID (só enfileirada) → first_paid=False
    enr, coord = _enrollment(_S.FEE_SCHEDULED)
    _seed_fee(enr, "now", PaymentRequest.Status.QUEUED)
    _seed_fee(enr, "due", PaymentRequest.Status.QUEUED)
    with pytest.raises(Conflict) as exc:
        _conclude(enr, coord)
    assert exc.value.extra["missing"] == ["first_fee_paid"]


# ── apply_fee_paid: hook do webhook (casa por PREFIXO, muda status só na 1ª) ──


def test_apply_fee_paid_primeira_parcela_vira_fee_paid(monkeypatch):
    enr, _ = _enrollment(_S.AWAITING_RELEASE)
    monkeypatch.setattr(es, "_notify_fee_event", lambda *a, **k: None)
    ok = es.apply_fee_paid(
        enr, external_reference=f"fee_enr_{enr.external_id}_now", amount="100"
    )
    assert ok is True
    enr.refresh_from_db()
    assert enr.status == _S.FEE_PAID


def test_apply_fee_paid_retry_rN_ainda_casa_por_prefixo(monkeypatch):
    enr, _ = _enrollment(_S.AWAITING_RELEASE)
    monkeypatch.setattr(es, "_notify_fee_event", lambda *a, **k: None)
    # re-tentativa pós-falha carrega sufixo _r3 — o prefixo TEM que casar mesmo assim
    ok = es.apply_fee_paid(
        enr, external_reference=f"fee_enr_{enr.external_id}_now_r3", amount="100"
    )
    assert ok is True
    enr.refresh_from_db()
    assert enr.status == _S.FEE_PAID


def test_apply_fee_paid_segunda_parcela_so_notifica(monkeypatch):
    enr, _ = _enrollment(_S.AWAITING_RELEASE)
    monkeypatch.setattr(es, "_notify_fee_event", lambda *a, **k: None)
    ok = es.apply_fee_paid(
        enr, external_reference=f"fee_enr_{enr.external_id}_due", amount="100"
    )
    assert ok is True
    enr.refresh_from_db()
    assert enr.status == _S.AWAITING_RELEASE  # 2ª parcela NÃO mexe no status


def test_apply_fee_paid_ref_de_outra_matricula_retorna_false(monkeypatch):
    enr, _ = _enrollment(_S.AWAITING_RELEASE)
    monkeypatch.setattr(es, "_notify_fee_event", lambda *a, **k: None)
    ok = es.apply_fee_paid(
        enr, external_reference=f"fee_enr_{uuid.uuid4()}_now", amount="100"
    )
    assert ok is False
    enr.refresh_from_db()
    assert enr.status == _S.AWAITING_RELEASE
