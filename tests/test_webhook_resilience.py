"""Resiliência do caminho do dinheiro (auditoria 2026-08-16, R1/R2/R4):

- Válvula anti-veneno: a fila de webhook do Asaas é sequencial — um handler com erro
  PERSISTENTE pro mesmo pagamento não pode 500ar pra sempre (o gateway desativa a
  sincronização). Na N-ésima entrega falhada, engole com 200 e quarentena no ledger.
- Reconciliação ativa: quando o webhook não chega, `refresh_payout` pergunta o status
  direto na API do Asaas (era o único caminho sem leitura ativa — QR e boleto já tinham).
"""

from decimal import Decimal

import pytest

from integrations.bank.asaas import webhooks
from integrations.bank.asaas.models import Payment, WebhookEvent

pytestmark = pytest.mark.django_db


def _paid_charge(pid="pay_x1", asaas_id="asaas_x1"):
    return Payment.objects.create(
        payment_id=pid, kind=Payment.Kind.CHARGE, amount=Decimal("10.00"), status="PENDING", asaas_id=asaas_id
    )


def _payload(asaas_id="asaas_x1", event="PAYMENT_CONFIRMED"):
    return {"event": event, "payment": {"id": asaas_id, "externalReference": "pay_x1"}}


def test_valvula_erro_transitorio_continua_propagando(monkeypatch):
    """G4 preservado: nas primeiras entregas, falha do handler propaga (500 → Asaas re-tenta)."""
    _paid_charge()

    def _boom(*a, **k):
        raise RuntimeError("hub sem seed")

    monkeypatch.setattr(webhooks.core_hooks, "dispatch", _boom)
    with pytest.raises(RuntimeError):
        webhooks.handle_event(_payload())
    # o evento ficou no ledger, não-encaminhado (é o contador da válvula)
    assert WebhookEvent.objects.filter(forwarded_ok=False).count() == 1


def test_valvula_quarentena_na_enesima_falha(monkeypatch):
    """Erro PERSISTENTE: na _QUARANTINE_AFTER-ésima entrega, engole (sem raise) e quarentena."""
    _paid_charge()

    def _boom(*a, **k):
        raise RuntimeError("hub sem seed")

    monkeypatch.setattr(webhooks.core_hooks, "dispatch", _boom)
    # entregas 1..N-1: propagam (o Asaas re-tentaria)
    for _ in range(webhooks._QUARANTINE_AFTER - 1):
        with pytest.raises(RuntimeError):
            webhooks.handle_event(_payload())
    # entrega N: válvula abre — retorna o row sem levantar (view responde 200)
    row = webhooks.handle_event(_payload())
    assert row is not None
    assert row.forwarded_ok is False  # quarentenado no ledger, visível no /webhooks/unconsumed


def test_refresh_payout_done_vira_paid(monkeypatch):
    """Plano B do webhook: transfer DONE na API → Payment local PAID, sem esperar TRANSFER_DONE."""
    from integrations.bank.asaas import payout as asaas_payout

    Payment.objects.create(
        payment_id="po_1",
        kind=Payment.Kind.PIXKEY,
        amount=Decimal("50.00"),
        status="SUBMITTED",
        asaas_id="tr_1",
    )

    class _FakeClient:
        async def get_transfer(self, transfer_id):
            assert transfer_id == "tr_1"
            return {"status": "DONE"}

    monkeypatch.setattr(asaas_payout, "get_client", lambda: _FakeClient())
    row = asaas_payout.refresh_payout("po_1")
    assert row.status == "PAID"


def test_refresh_payout_saldo_insuficiente_nao_e_terminal(monkeypatch):
    """FAILED por saldo → AWAITING_BALANCE (a fila re-tenta; não perde dinheiro)."""
    from integrations.bank.asaas import payout as asaas_payout

    Payment.objects.create(
        payment_id="po_2",
        kind=Payment.Kind.PIXKEY,
        amount=Decimal("50.00"),
        status="SUBMITTED",
        asaas_id="tr_2",
    )

    class _FakeClient:
        async def get_transfer(self, transfer_id):
            return {"status": "FAILED", "failReason": "Saldo insuficiente na conta"}

    monkeypatch.setattr(asaas_payout, "get_client", lambda: _FakeClient())
    row = asaas_payout.refresh_payout("po_2")
    assert row.status == "AWAITING_BALANCE"
