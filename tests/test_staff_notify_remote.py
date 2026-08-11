"""Painel staff em NOTIFY_MODE=remote (Fase 2): /history proxia o notify-server e as mutações
de Template/Trigger fazem dual-write (local + push, atômico).

Transporte mockado na `notify.sdk.client._request` (ponto único de rede — mesma convenção do
test_notify_sdk_remote). Cobre: mapeamento do history, params dos filtros, 502 NOTIFY_SERVER_DOWN,
rollback local quando o push falha e o modo local intocado (zero HTTP).
"""

import json

import pytest

pytestmark = pytest.mark.django_db


class _FakeResp:
    """Resposta httpx mínima p/ o monkeypatch da `client._request`."""

    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data
        self.text = text

    def json(self):
        if self._json is None:
            raise ValueError("sem json")
        return self._json


@pytest.fixture
def staff_headers():
    """Bearer de um SUPERUSER — todas as rotas de staff exigem `require_superuser`."""
    from users.auth.jwt import service as jwt_service
    from users.auth.models import User

    user = User.objects.create_superuser(password="x")
    tokens = jwt_service.issue(str(user.external_id), [])
    return {"HTTP_AUTHORIZATION": f"Bearer {tokens['access_token']}"}


@pytest.fixture
def remote(settings):
    settings.NOTIFY_MODE = "remote"
    settings.NOTIFY_SERVER_URL = "http://notify.test"
    settings.NOTIFY_API_KEY = "test-key"
    settings.NOTIFY_TIMEOUT = 5.0
    settings.NOTIFY_SYNC_TIMEOUT = 33.0
    settings.NOTIFY_ACCOUNT_SLUG = "supletivo"
    return settings


@pytest.fixture
def http(monkeypatch):
    """Troca a `client._request` por um fake: grava a chamada e devolve a resposta enfileirada."""
    state = {"calls": [], "responses": []}

    def fake_request(method, path, *, json=None, params=None, timeout=None):
        state["calls"].append(
            {"method": method, "path": path, "json": json, "params": params}
        )
        if not state["responses"]:
            raise AssertionError("nenhuma resposta enfileirada p/ _request")
        return state["responses"].pop(0)

    monkeypatch.setattr("notify.sdk.client._request", fake_request)
    return state


@pytest.fixture
def no_http(monkeypatch):
    """Modo local NÃO pode tocar a rede: qualquer chamada do SDK derruba o teste."""
    monkeypatch.setattr(
        "notify.sdk.client._request",
        lambda *a, **k: pytest.fail("modo local não deveria chamar o notify-server"),
    )


def _put(client, path, body, headers):
    return client.put(
        path, data=json.dumps(body), content_type="application/json", **headers
    )


# ── GET /history (proxy) ─────────────────────────────────────────────────────


def test_history_remote_mapeia_campos(client, staff_headers, remote, http):
    """Proxy: NotificationOut ampliado do servidor → shape local de 18 campos (mesmos nomes)."""
    row = {
        "external_id": "356f6f00-acf1-46f3-bf3a-a96272e0e2b8",
        "caller": "event:lead.paid",
        "recipient_phone": "5511920062177",
        "recipient_email": None,
        "title": "Parabéns",
        "subject": None,
        "text": "Oi Maria",
        "want_whatsapp": True,
        "want_email": False,
        "want_tts": True,
        "whatsapp_status": "sent",
        "email_status": "skipped",
        "tts_status": "failed",
        "whatsapp_error": None,
        "email_error": None,
        "tts_error": "voz indisponível",
        "attempts": 2,
        "created_at": "2026-07-18T02:24:09.666372+00:00",
        # extras do servidor (idempotency_key/media/gender/...) são simplesmente ignorados
        "idempotency_key": "chave-x",
        "media_url": None,
        "gender": "F",
    }
    http["responses"].append(_FakeResp(200, [row]))
    resp = client.get(
        "/api/v1/staff/notify/history?caller=event:lead.paid&whatsapp_status=sent&limit=5",
        **staff_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    out = data[0]
    assert out["external_id"] == row["external_id"]
    assert out["caller"] == "event:lead.paid"
    assert out["recipient_phone"] == "5511920062177"
    assert out["text"] == "Oi Maria"
    assert out["want_tts"] is True
    assert out["tts_status"] == "failed"
    assert out["tts_error"] == "voz indisponível"
    assert out["attempts"] == 2
    assert out["created_at"] == row["created_at"]
    assert "idempotency_key" not in out  # shape local: 18 campos, sem extras
    # filtros e limit repassados ao servidor
    call = http["calls"][0]
    assert call["method"] == "GET"
    assert call["path"] == "/v1/notifications"
    assert call["params"]["caller"] == "event:lead.paid"
    assert call["params"]["whatsapp_status"] == "sent"
    assert call["params"]["limit"] == 5


def test_history_remote_servidor_fora_vira_502(client, staff_headers, remote, http):
    http["responses"].append(_FakeResp(500, None, text="<html>erro</html>"))
    resp = client.get("/api/v1/staff/notify/history", **staff_headers)
    assert resp.status_code == 502
    assert resp.json()["code"] == "NOTIFY_SERVER_DOWN"


# ── PUT /templates/{event} (dual-write) ──────────────────────────────────────

_BODY = {"body_md": "Oi {nome}, chegou!", "is_tts": True, "channels": "whatsapp"}

# valores distintos em TODOS os 11 campos que `_server_template_payload` monta — um typo em
# qualquer chave do payload (ou um campo esquecido) quebra a igualdade exata abaixo.
_BODY_FULL = {
    "title": "Novo Template",
    "subject": "Assunto novo",
    "body_md": "Oi {nome}, chegou!",
    "is_tts": True,
    "storytelling": True,
    "story_prompt": "conte uma historia de superacao",
    "channels": "whatsapp",
    "media_url": "http://x/img.png",
    "media_type": "image",
    "mail_template": "custom-template",
    "notes": "nota interna do staff",
}


def test_put_template_remote_dual_write(client, staff_headers, remote, http):
    """Escreve local E faz PUT full no servidor (conta NOTIFY_ACCOUNT_SLUG) — TODOS os 11 campos
    de `_server_template_payload` vão no payload, com valores distintos."""
    from notify.models import Template

    http["responses"].append(_FakeResp(200, {"event": "ev.x"}))
    resp = _put(
        client, "/api/v1/staff/notify/templates/ev.x", _BODY_FULL, staff_headers
    )
    assert resp.status_code == 200
    assert Template.objects.filter(event="ev.x").exists()
    call = http["calls"][0]
    assert call["method"] == "PUT"
    assert call["path"] == "/v1/staff/templates/supletivo/ev.x"
    assert call["json"] == {
        "title": "Novo Template",
        "subject": "Assunto novo",
        "body_md": "Oi {nome}, chegou!",
        "is_tts": True,
        "storytelling": True,
        "story_prompt": "conte uma historia de superacao",
        "channels": "whatsapp",
        "media_url": "http://x/img.png",
        "media_type": "image",
        "mail_template": "custom-template",
        "notes": "nota interna do staff",
    }


def test_put_template_remote_push_falha_rollback(client, staff_headers, remote, http):
    """Push 5xx → 502 NOTIFY_SERVER_DOWN e a escrita local é DESFEITA (espelho coeso)."""
    from notify.models import Template

    http["responses"].append(_FakeResp(503, None, text="down"))
    resp = _put(client, "/api/v1/staff/notify/templates/ev.x", _BODY, staff_headers)
    assert resp.status_code == 502
    assert resp.json()["code"] == "NOTIFY_SERVER_DOWN"
    assert not Template.objects.filter(event="ev.x").exists()


# ── PATCH (parcial local → PUT full no servidor) ─────────────────────────────


def test_patch_remote_faz_put_full_do_estado(client, staff_headers, remote, http):
    """O servidor não tem PATCH: o parcial vira PUT do estado RESULTANTE (campos não tocados vão)."""
    from notify.models import Template

    Template.objects.create(event="ev.x", body_md="Oi {nome}", is_tts=False)
    http["responses"].append(_FakeResp(200, {"event": "ev.x"}))
    resp = client.patch(
        "/api/v1/staff/notify/templates/ev.x",
        data=json.dumps({"is_tts": True}),
        content_type="application/json",
        **staff_headers,
    )
    assert resp.status_code == 200
    call = http["calls"][0]
    assert call["method"] == "PUT"
    assert call["json"]["is_tts"] is True
    assert call["json"]["body_md"] == "Oi {nome}"  # não veio no PATCH, vai no full
    assert Template.objects.get(event="ev.x").is_tts is True


def test_patch_remote_push_falha_rollback(client, staff_headers, remote, http):
    from notify.models import Template

    Template.objects.create(event="ev.x", body_md="Oi {nome}", is_tts=False)
    http["responses"].append(_FakeResp(500, None, text="erro"))
    resp = client.patch(
        "/api/v1/staff/notify/templates/ev.x",
        data=json.dumps({"is_tts": True}),
        content_type="application/json",
        **staff_headers,
    )
    assert resp.status_code == 502
    assert Template.objects.get(event="ev.x").is_tts is False  # rollback


# ── PUT .../trigger ──────────────────────────────────────────────────────────


def test_put_trigger_remote_dual_write(client, staff_headers, remote, http):
    from notify.models import Template, Trigger

    Template.objects.create(event="ev.x", body_md="Oi")
    http["responses"].append(_FakeResp(200, {"active": False}))
    resp = _put(
        client,
        "/api/v1/staff/notify/templates/ev.x/trigger",
        {
            "fires_on": "lead.paid",
            "source": "manual-staff",
            "delay_minutes": -3,
            "active": False,
        },
        staff_headers,
    )
    assert resp.status_code == 200
    call = http["calls"][0]
    assert call["method"] == "PUT"
    assert call["path"] == "/v1/staff/templates/supletivo/ev.x/trigger"
    assert call["json"]["fires_on"] == "lead.paid"
    assert call["json"]["source"] == "manual-staff"  # nunca era asserido antes
    assert call["json"]["delay_minutes"] == 0  # clamp ≥0 vale nos dois lados
    assert call["json"]["active"] is False
    assert Trigger.objects.get(template__event="ev.x").active is False


def test_put_trigger_remote_push_falha_rollback(client, staff_headers, remote, http):
    from notify.models import Template, Trigger

    t = Template.objects.create(event="ev.x", body_md="Oi")
    Trigger.objects.create(template=t, active=True)
    http["responses"].append(_FakeResp(502, None, text="bad gateway"))
    resp = _put(
        client,
        "/api/v1/staff/notify/templates/ev.x/trigger",
        {"active": False},
        staff_headers,
    )
    assert resp.status_code == 502
    assert resp.json()["code"] == "NOTIFY_SERVER_DOWN"
    assert Trigger.objects.get(template=t).active is True  # rollback


# ── DELETE ───────────────────────────────────────────────────────────────────


def test_delete_remote_dual_write(client, staff_headers, remote, http):
    from notify.models import Template

    Template.objects.create(event="ev.x", body_md="Oi")
    http["responses"].append(_FakeResp(200, {"deleted": "ev.x"}))
    resp = client.delete("/api/v1/staff/notify/templates/ev.x", **staff_headers)
    assert resp.status_code == 200
    assert not Template.objects.filter(event="ev.x").exists()
    call = http["calls"][0]
    assert call["method"] == "DELETE"
    assert call["path"] == "/v1/staff/templates/supletivo/ev.x"


def test_delete_remote_404_no_servidor_e_tolerado(client, staff_headers, remote, http):
    """404 lá = já não existia — delete idempotente: local apaga e a resposta é 200."""
    from notify.models import Template

    Template.objects.create(event="ev.x", body_md="Oi")
    http["responses"].append(_FakeResp(404, {"detail": "Template não encontrado."}))
    resp = client.delete("/api/v1/staff/notify/templates/ev.x", **staff_headers)
    assert resp.status_code == 200
    assert not Template.objects.filter(event="ev.x").exists()


def test_delete_remote_push_falha_rollback(client, staff_headers, remote, http):
    from notify.models import Template

    Template.objects.create(event="ev.x", body_md="Oi")
    http["responses"].append(_FakeResp(500, None, text="erro"))
    resp = client.delete("/api/v1/staff/notify/templates/ev.x", **staff_headers)
    assert resp.status_code == 502
    assert Template.objects.filter(event="ev.x").exists()  # rollback: nada apagado


# ── POST .../restore-seed ────────────────────────────────────────────────────

_SEED_EVENT = (
    "candidate.awaiting_approval"  # primeiro evento do notify/seed/templates.md
)


def test_restore_seed_remote_faz_put_full(client, staff_headers, remote, http):
    from notify.models import Template

    http["responses"].append(_FakeResp(200, {"event": _SEED_EVENT}))
    resp = client.post(
        f"/api/v1/staff/notify/templates/{_SEED_EVENT}/restore-seed", **staff_headers
    )
    assert resp.status_code == 200
    t = Template.objects.get(event=_SEED_EVENT)
    call = http["calls"][0]
    assert call["method"] == "PUT"
    assert call["path"] == f"/v1/staff/templates/supletivo/{_SEED_EVENT}"
    assert call["json"]["body_md"] == t.body_md  # teor restaurado vai FULL pro servidor


def test_restore_seed_remote_push_falha_rollback(client, staff_headers, remote, http):
    from notify.models import Template

    http["responses"].append(_FakeResp(500, None, text="erro"))
    resp = client.post(
        f"/api/v1/staff/notify/templates/{_SEED_EVENT}/restore-seed", **staff_headers
    )
    assert resp.status_code == 502
    assert not Template.objects.filter(event=_SEED_EVENT).exists()  # rollback
