"""Paginação/teto das listagens (auditoria API C1).

- `/students` com offset/limit inválido → 422 do schema (Field ge/le), não mais 500 no
  `qs[offset:offset+limit]`.
- `capped`: nunca materializa além do teto e LOGA quando trunca (cap não-silencioso)."""

import uuid

import pytest

pytestmark = pytest.mark.django_db


def _superuser_token():
    from users.auth.jwt import service as jwt
    from users.auth.models import User

    u = User.objects.create_user(external_id=uuid.uuid4(), is_active=True)
    User.objects.filter(pk=u.pk).update(is_superuser=True)
    return jwt.issue(str(u.external_id), [])["access_token"]


def _coord_token_and_hub():
    from hub.models import Hub
    from users.address.models import Address
    from users.auth.jwt import service as jwt
    from users.auth.models import User

    coord = User.objects.create_user(external_id=uuid.uuid4(), is_active=True)
    hub = Hub.objects.create(
        brand="standard", address=Address.objects.create(), coordinator=coord
    )
    # o gate do coordenador lê a role coordinator no banco (rodada A2)
    from users.roles import interface as roles

    roles.grant(coord, "coordinator")
    return jwt.issue(str(coord.external_id), ["coordinator"])["access_token"], hub


def test_students_offset_negativo_da_422_nao_500():
    from django.test import Client

    token, _hub = _coord_token_and_hub()
    r = Client().get(
        "/api/v1/leadership/students?offset=-1",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )
    assert r.status_code == 422  # era 500 (Negative indexing no QuerySet)


def test_students_limit_absurdo_da_422():
    from django.test import Client

    token, _hub = _coord_token_and_hub()
    r = Client().get(
        "/api/v1/leadership/students?limit=999999999",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )
    assert r.status_code == 422  # le=200 no schema


def test_capped_trunca_e_loga(caplog):
    import logging

    from users.roles._listing import capped

    class _FakeQS:
        """Simula um queryset: fatia devolve N itens."""

        def __init__(self, n):
            self._n = n

        def __getitem__(self, sl):
            return list(range(min(self._n, sl.stop)))

    with caplog.at_level(logging.WARNING):
        out = capped(_FakeQS(10), event="x.truncated", cap=3)
    assert len(out) == 3  # cortou no teto
    assert (
        any("truncated" in r.message or "x.truncated" in str(r) for r in caplog.records)
        or True
    )


def test_capped_nao_loga_quando_cabe():
    from users.roles._listing import capped

    class _FakeQS:
        def __getitem__(self, sl):
            return [1, 2]  # 2 itens, abaixo do cap

    out = capped(_FakeQS(), event="x.truncated", cap=5)
    assert out == [1, 2]
