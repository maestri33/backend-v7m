"""GET /staff/webhooks/unconsumed (vigia do ledger, auditoria R4) — testes mínimos por endpoint
(AGENTS.md): sucesso, anônimo 401, autenticado sem permissão 403, e o sinal que importa
(`money_count` só conta PAYMENT_CONFIRMED/RECEIVED órfãos, não o ruído de no-op).
"""

import pytest

pytestmark = pytest.mark.django_db


@pytest.fixture
def staff_headers():
    from users.auth.jwt import service as jwt_service
    from users.auth.models import User

    user = User.objects.create_superuser(password="x")
    tokens = jwt_service.issue(str(user.external_id), [])
    return {"HTTP_AUTHORIZATION": f"Bearer {tokens['access_token']}"}


@pytest.fixture
def non_staff_headers():
    import uuid

    from users.auth.jwt import service as jwt_service
    from users.auth.models import User

    user = User.objects.create_user(external_id=uuid.uuid4())
    tokens = jwt_service.issue(str(user.external_id), [])
    return {"HTTP_AUTHORIZATION": f"Bearer {tokens['access_token']}"}


def _orphan(event: str, pid: str = "asaas_1"):
    from integrations.bank.asaas.models import WebhookEvent

    return WebhookEvent.objects.create(
        event=event, payload={"payment": {"id": pid, "externalReference": "ref_1"}}
    )


def test_anonimo_401(client):
    resp = client.get("/api/v1/staff/webhooks/unconsumed")
    assert resp.status_code == 401


def test_autenticado_sem_superuser_403(client, non_staff_headers):
    resp = client.get("/api/v1/staff/webhooks/unconsumed", **non_staff_headers)
    assert resp.status_code == 403


def test_money_count_conta_so_dinheiro(client, staff_headers):
    _orphan("PAYMENT_CONFIRMED")
    _orphan("PAYMENT_CREATED", pid="asaas_2")  # ruído esperado: no-op nunca é encaminhado
    resp = client.get("/api/v1/staff/webhooks/unconsumed", **staff_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["money_count"] == 1
    assert data["total_unconsumed"] == 2
    assert data["money_events"][0]["asaas_payment_id"] == "asaas_1"
    assert data["money_events"][0]["external_reference"] == "ref_1"
