"""Router Financeiro e Payouts (Staff)."""

from __future__ import annotations

from decimal import Decimal
from ninja import File, Form, Header, Router
from ninja.errors import HttpError
from ninja.files import UploadedFile

from api.auth import require_superuser
from finance import interface as finance_iface
from finance.interface import commissions as finance_closing
from finance.interface import manual as finance_manual
from integrations.bank.asaas import onboarding as asaas_onboarding
from users.exceptions import ValidationError

router = Router(tags=["staff"])

_MANUAL_PAYMENT_DETAIL = {
    "invalid_amount": "Valor inválido.",
    "amount_must_be_positive": "O valor deve ser positivo.",
    "pix_key_required": "Chave PIX obrigatória.",
    "line_code_required": "Linha digitável do boleto obrigatória.",
}


def _raise_manual_payment_error(exc: Exception):
    slug = str(exc)
    detail = _MANUAL_PAYMENT_DETAIL.get(slug, slug)
    raise ValidationError(detail, code=f"PAYMENT_{slug.upper()}") from exc


@router.get("/finance/balance", summary="Saldo da conta Asaas")
def finance_balance(request):
    """Saldo da conta Asaas (read-only)."""
    require_superuser(request.auth)
    return asaas_onboarding.account_balance()


@router.get("/finance/summary", summary="Resumo financeiro")
def finance_summary(request):
    """Resumo de comissões e fila de saída."""
    require_superuser(request.auth)
    return finance_iface.summary()


@router.get("/finance/commissions", summary="Listagem de comissões")
def finance_commissions(request, status: str | None = None):
    """Comissões do sistema por status."""
    require_superuser(request.auth)
    return finance_iface.list_commissions(status=status)


@router.get("/finance/payouts", summary="Solicitações de pagamento")
def finance_payouts(request, status: str | None = None, kind: str | None = None):
    """Fila de solicitações de pagamento / payouts."""
    require_superuser(request.auth)
    return finance_iface.list_payment_requests(status=status, kind=kind)


@router.post("/finance/payments", summary="Pagamento avulso (PIX/Boleto)")
def create_manual_payment(
    request,
    kind: str = Form(...),
    amount: str | None = Form(None),
    description: str | None = Form(None),
    supplier_name: str | None = Form(None),
    pix_key: str | None = Form(None),
    boleto_line: str | None = Form(None),
    receipt: UploadedFile | None = File(None),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    """Enfileira pagamento avulso protegido por idempotência."""
    require_superuser(request.auth)
    if not (idempotency_key or "").strip():
        raise HttpError(422, "IDEMPOTENCY_KEY_REQUIRED")

    receipt_path = None
    if receipt is not None:
        from core.media import save_media

        ext = (getattr(receipt, "name", "") or "").rsplit(".", 1)[-1].lower() or "jpg"
        receipt_path = save_media(prefix="receipt", data=receipt.read(), ext=ext)

    try:
        if kind == "pix":
            pr = finance_manual.request_pix_payment(
                amount=amount,
                pix_key=pix_key,
                supplier_name=supplier_name,
                description=description,
                receipt=receipt_path,
                idempotency_key=idempotency_key,
            )
        elif kind == "boleto":
            pr = finance_manual.request_boleto_payment(
                line_code=boleto_line,
                amount=amount,
                supplier_name=supplier_name,
                description=description,
                receipt=receipt_path,
                idempotency_key=idempotency_key,
            )
        else:
            raise ValidationError(
                "kind deve ser 'pix' ou 'boleto'.", code="PAYMENT_INVALID_KIND"
            )
    except finance_manual.ManualPaymentError as exc:
        _raise_manual_payment_error(exc)
    return {
        "external_id": str(pr.external_id),
        "kind": pr.kind,
        "method": pr.method,
        "amount": str(pr.amount),
        "status": pr.status,
        "external_reference": pr.external_reference,
        "receipt": pr.receipt,
    }


@router.post("/finance/closing/run", summary="Executar fechamento semanal")
def run_closing(request):
    """Executa o fechamento semanal de comissões."""
    require_superuser(request.auth)
    return finance_closing.run_weekly_closing()


@router.get("/finance/closing/health", summary="Saúde do fechamento semanal")
def closing_health(request):
    """Cruza saldo do Asaas com obrigações pendentes."""
    require_superuser(request.auth)
    obligation = finance_iface.closing_obligation()
    estimated = Decimal(obligation["obrigacao_estimada"])

    balance = asaas_onboarding.account_balance()
    saldo = balance.get("balance") if isinstance(balance, dict) else None
    if saldo is None:
        return {
            **obligation,
            "saldo": None,
            "suficiente": None,
            "deficit": None,
            "balance_error": balance.get("error")
            if isinstance(balance, dict)
            else True,
        }
    saldo_dec = Decimal(str(saldo)).quantize(Decimal("0.01"))
    deficit = estimated - saldo_dec
    return {
        **obligation,
        "saldo": str(saldo_dec),
        "suficiente": saldo_dec >= estimated,
        "deficit": str(deficit) if deficit > 0 else "0.00",
    }
