"""Contrato de pay_fee/schedule_fee (auditoria API P1): os endpoints declaravam EnrollmentFeesOut
(só os 4 campos de `fees`), mas o service devolve {external_id, status, fees:{...}}. O Ninja não
achava esses campos e caía nos defaults → envelope VAZIO (first_paid:false mesmo após pagar). O
schema certo é EnrollmentFeeActionOut. Este teste serializa o dict REAL do service pelo schema e
prova que o bloco `fees` sobrevive (não é defaultado)."""

from api.leadership import EnrollmentFeeActionOut


def test_fee_action_out_preserva_o_bloco_fees():
    """O dict que pay_fee/schedule_fee retornam, serializado pelo schema, mantém first_paid=True."""
    service_return = {
        "external_id": "enr-123",
        "status": "fee_paid",
        "fees": {
            "first": {"amount": "50.00", "status": "PAID", "paid": True},
            "second": None,
            "first_paid": True,
            "second_scheduled": False,
        },
    }
    out = EnrollmentFeeActionOut(**service_return)
    dumped = out.model_dump()
    assert dumped["external_id"] == "enr-123"
    assert dumped["status"] == "fee_paid"
    # o bug: com EnrollmentFeesOut, first_paid vinha False (default) — o pagamento sumia da resposta
    assert dumped["fees"]["first_paid"] is True
    assert dumped["fees"]["first"]["status"] == "PAID"


def test_fees_out_sozinho_perderia_os_campos_externos():
    """Prova do bug ANTIGO: o schema vazio (EnrollmentFeesOut) não tem external_id/status/fees —
    alimentado com o dict do service, os 4 campos dele caem nos defaults (envelope vazio)."""
    from api.leadership import EnrollmentFeesOut

    service_return = {
        "external_id": "enr-123",
        "status": "fee_paid",
        "fees": {"first_paid": True},
    }
    dumped = EnrollmentFeesOut(**service_return).model_dump()
    # o schema errado ignora external_id/status e não enxerga first_paid (que está aninhado em fees)
    assert dumped["first_paid"] is False
    assert "external_id" not in dumped
