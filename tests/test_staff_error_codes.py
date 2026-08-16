"""Contrato de erro do grupo `staff`: TODO 4xx sai no envelope `{detail, code}` com um `code`
UPPER_SNAKE estável — nunca o fallback `ERROR` (que quebrava o `switch(code)` do front admin).

Espelha os grupos de funil (clients/collaborators/leadership), que já saem com `code` próprio.
"""

import uuid

import pytest

pytestmark = pytest.mark.django_db


@pytest.fixture
def staff_headers():
    """Bearer de um SUPERUSER — todas as rotas de staff exigem `require_superuser`."""
    from users.auth.jwt import service as jwt_service
    from users.auth.models import User

    user = User.objects.create_superuser(password="x")
    tokens = jwt_service.issue(str(user.external_id), [])
    return {"HTTP_AUTHORIZATION": f"Bearer {tokens['access_token']}"}


def test_integration_inexistente_tem_code(client, staff_headers):
    """GET /integrations/<nome desconhecido> → 404 `INTEGRATION_NOT_FOUND` (era `ERROR`)."""
    resp = client.get("/api/v1/staff/integrations/naoexiste", **staff_headers)
    assert resp.status_code == 404
    data = resp.json()
    assert data.get("code") == "INTEGRATION_NOT_FOUND"


def test_hub_inexistente_tem_code(client, staff_headers):
    """GET /leads?hub=<uuid inexistente> → 404 `HUB_NOT_FOUND` (era `ERROR`)."""
    resp = client.get(f"/api/v1/staff/leads?hub={uuid.uuid4()}", **staff_headers)
    assert resp.status_code == 404
    data = resp.json()
    assert data.get("code") == "HUB_NOT_FOUND"


@pytest.mark.parametrize("data", [{"code": "INTEGRATION_NOT_FOUND"}])
def test_code_nunca_e_fallback(client, staff_headers, data):
    """Sanidade: o code padronizado nunca é o fallback `ERROR` nem ausente."""
    resp = client.get("/api/v1/staff/integrations/naoexiste", **staff_headers)
    code = resp.json().get("code")
    assert code not in (None, "ERROR")
    assert code == data["code"]


def test_pagamento_avulso_sem_idempotency_key_tem_code(client, staff_headers):
    """DINHEIRO (auditoria A5): POST /finance/payments sem o header Idempotency-Key → 422 com code
    estável IDEMPOTENCY_KEY_REQUIRED (era HttpError cru → code `ERROR`, e o front repetia a ação)."""
    resp = client.post(
        "/api/v1/staff/finance/payments",
        data={"kind": "pix", "amount": "10.00", "pix_key": "x@y.z"},
        **staff_headers,
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body.get("code") == "IDEMPOTENCY_KEY_REQUIRED"
    assert body.get("code") != "ERROR"
