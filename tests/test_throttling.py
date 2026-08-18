"""Throttling das rotas públicas (auditoria API B1): o projeto não tinha throttling nenhum e
POST /clients/auth/check é anônimo e cria conta+OTP+WhatsApp por request. Agora um teto por IP
e uma cota diária por promotor no convite."""

import uuid

import pytest
from django.test import Client

pytestmark = pytest.mark.django_db


def _check_body():
    return {"phone": "11999990000", "send_otp": False}


def test_check_anonimo_estoura_o_teto_por_ip(monkeypatch):
    """Acima de THROTTLE_ANON_RATE requests do MESMO IP → 429 RATE_LIMITED (antes: ilimitado)."""
    from django.conf import settings

    monkeypatch.setattr(settings, "THROTTLE_ANON_RATE", "3/m")

    c = Client(REMOTE_ADDR="203.0.113.7")
    codes = []
    for _ in range(5):
        r = c.post(
            "/api/v1/clients/auth/check",
            data=_check_body(),
            content_type="application/json",
        )
        codes.append(r.status_code)

    assert codes.count(429) >= 1, f"nunca throttlou: {codes}"
    # o 429 sai no envelope padrão com code estável
    last = c.post(
        "/api/v1/clients/auth/check",
        data=_check_body(),
        content_type="application/json",
    )
    assert last.status_code == 429
    assert last.json().get("code") == "RATE_LIMITED"


def test_throttle_cobre_collaborators_check(monkeypatch):
    """Verificação adversarial: o throttle é default do build_group, então cobre TODOS os grupos —
    não só clients. collaborators/auth/check (anônimo, dispara OTP) também é limitado por IP."""
    from django.conf import settings

    monkeypatch.setattr(settings, "THROTTLE_ANON_RATE", "3/m")
    c = Client(REMOTE_ADDR="203.0.113.9")
    codes = [
        c.post(
            "/api/v1/collaborators/auth/check",
            data={"phone": "11999990000", "send_otp": False},
            content_type="application/json",
        ).status_code
        for _ in range(6)
    ]
    assert codes.count(429) >= 1, f"collaborators/check sem throttle: {codes}"


def test_ips_diferentes_nao_compartilham_o_teto(monkeypatch):
    """O teto é POR IP: um IP estourado não bloqueia outro cliente."""
    from django.conf import settings

    monkeypatch.setattr(settings, "THROTTLE_ANON_RATE", "2/m")

    a = Client(REMOTE_ADDR="203.0.113.1")
    for _ in range(3):
        a.post(
            "/api/v1/clients/auth/check",
            data=_check_body(),
            content_type="application/json",
        )
    b = Client(REMOTE_ADDR="203.0.113.2")
    r = b.post(
        "/api/v1/clients/auth/check",
        data=_check_body(),
        content_type="application/json",
    )
    assert r.status_code != 429


def test_invite_respeita_cota_diaria(monkeypatch):
    """Convite acima da cota diária do promotor → Forbidden INVITE_QUOTA_EXCEEDED."""
    from django.conf import settings

    from users.exceptions import Forbidden

    monkeypatch.setattr(settings, "INVITE_DAILY_QUOTA", 2)

    import users.roles.promoter.service as ps
    from users.auth import service as auth_iface
    from users.profiles import interface as profiles
    from users.roles.training import service as training_iface

    # neutraliza tudo que não é a cota (validações externas + envio)
    monkeypatch.setattr(ps, "_send_lead_invite", lambda *a, **k: "notif-1")
    monkeypatch.setattr(training_iface, "is_locked", lambda *a, **k: False)
    monkeypatch.setattr(
        auth_iface, "check_phone_whatsapp", lambda phone: (True, "11999990000")
    )
    monkeypatch.setattr(profiles, "exists_phone", lambda *a, **k: False)

    class _Prom:
        status = ps.Promoter.Status.ACTIVE
        user = type("U", (), {"external_id": uuid.uuid4()})()

    prom = _Prom()

    # as 2 primeiras passam (cota=2), a 3ª estoura
    ps.invite_lead(promoter=prom, phone="11999990001")
    ps.invite_lead(promoter=prom, phone="11999990002")
    with pytest.raises(Forbidden) as exc:
        ps.invite_lead(promoter=prom, phone="11999990003")
    assert exc.value.code == "INVITE_QUOTA_EXCEEDED"
