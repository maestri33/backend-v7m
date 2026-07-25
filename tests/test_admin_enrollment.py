"""Admin de matrícula (`users/admin.py`) — o fluxo da taxa no /admin (Victor 2026-07-25).

Cobre o gate de superuser, o painel read-only com os botões e o roteamento das ações pra MESMA
camada de serviço da API leadership — inclusive erro de domínio virando mensagem (sem 500).
"""

import uuid

import pytest
from django.urls import reverse

from tests.test_money_guards import _PLAN_A_VISTA, _enrollment_awaiting_release

pytestmark = pytest.mark.django_db

# valores fictícios de teste — nada aqui é segredo real (GitGuardian: ignorar).
FAKE_PASSWORD = "senha ficticia so de teste"


def _superuser():
    from users.auth.models import User

    return User.objects.create_superuser(
        external_id=uuid.uuid4(), password=FAKE_PASSWORD
    )


def test_admin_matricula_exige_superuser(client):
    """Staff comum (sem superuser) não vê o painel nem executa ação — mexe em R$ real."""
    from users.auth.models import User

    _, enr = _enrollment_awaiting_release()
    staff = User.objects.create_user(external_id=uuid.uuid4(), is_staff=True)
    client.force_login(staff)

    change = client.get(reverse("admin:users_enrollment_change", args=[enr.pk]))
    assert change.status_code == 403

    action = client.get(
        reverse("admin:users_enrollment_action", args=[enr.pk, "conclude"])
    )
    assert action.status_code == 403


def test_admin_painel_mostra_as_tres_acoes(client):
    """A página da matrícula (read-only) exibe os 3 botões do fluxo da taxa."""
    _, enr = _enrollment_awaiting_release()
    client.force_login(_superuser())

    resp = client.get(reverse("admin:users_enrollment_change", args=[enr.pk]))
    assert resp.status_code == 200
    html = resp.content.decode()
    for kind in ("fee-pay", "fee-schedule", "conclude"):
        assert reverse("admin:users_enrollment_action", args=[enr.pk, kind]) in html, (
            f"botão {kind} ausente no painel"
        )


def test_admin_conclude_com_taxa_incompleta_mostra_erro(client):
    """Conclusão sem as 2 parcelas → Conflict FEES_INCOMPLETE vira mensagem (sem 500, sem promover)."""
    from users.roles.enrollment.models import Enrollment

    _, enr = _enrollment_awaiting_release()
    client.force_login(_superuser())

    resp = client.post(
        reverse("admin:users_enrollment_action", args=[enr.pk, "conclude"]),
        {"platform_login": "aluno1", "platform_password": FAKE_PASSWORD},
    )
    assert resp.status_code == 200
    assert "FEES_INCOMPLETE" in resp.content.decode()
    enr.refresh_from_db()
    assert enr.status == Enrollment.Status.AWAITING_RELEASE, "status andou sem taxa"


def test_admin_fee_pay_enfileira_pela_camada_de_servico(client, monkeypatch):
    """POST da 1ª parcela → enfileira UM PaymentRequest via serviço (idempotência preservada)."""
    from finance.models import PaymentRequest
    from users.roles.enrollment import service as es

    _, enr = _enrollment_awaiting_release()
    monkeypatch.setattr(es, "_plan_fee_qr", lambda qr, amount=None: dict(_PLAN_A_VISTA))
    client.force_login(_superuser())

    url = reverse("admin:users_enrollment_action", args=[enr.pk, "fee-pay"])
    resp = client.post(url, {"qr_code": "000201qr-teste", "amount": ""})
    assert resp.status_code == 302, resp.content.decode()[:500]
    assert PaymentRequest.objects.count() == 1

    # repost óbvio: o serviço devolve idempotente — segue 1 pedido só.
    client.post(url, {"qr_code": "000201qr-teste", "amount": ""})
    assert PaymentRequest.objects.count() == 1


def test_admin_acao_sem_coordenador_no_polo_avisa(client):
    """Polo sem coordenador → aviso claro em vez de quebrar (o serviço assina pelo coordenador)."""
    _, enr = _enrollment_awaiting_release()
    enr.hub.coordinator = None
    enr.hub.save(update_fields=["coordinator"])
    client.force_login(_superuser())

    resp = client.get(
        reverse("admin:users_enrollment_action", args=[enr.pk, "fee-pay"])
    )
    assert resp.status_code == 200
    assert "não tem coordenador" in resp.content.decode()
