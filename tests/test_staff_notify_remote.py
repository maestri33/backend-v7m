"""Painel staff em NOTIFY_MODE=remote: /history proxia o notify-server (a verdade dos ENVIOS mora
lá) e as mutações de Template/Trigger são LOCAL-ONLY — o catálogo é do supletivo, sem dual-write.

Transporte mockado na `notify.sdk.client._request` (ponto único de rede — mesma convenção do
test_notify_sdk_remote). Cobre: mapeamento do history, params dos filtros, 502 NOTIFY_SERVER_DOWN,
e que as mutações NÃO tocam o notify-server (nem em modo remote).
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
    settings.NOTIFY_URL = "http://notify.test"
    settings.NOTIFY_INSTANCE = "supletivo"
    settings.NOTIFY_API_KEY = "test-key"
    settings.NOTIFY_TIMEOUT = 5.0
    settings.NOTIFY_SYNC_TIMEOUT = 33.0
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


def test_history_local_intocado(client, staff_headers, no_http):
    """NOTIFY_MODE default (local): ORM local, zero HTTP."""
    resp = client.get("/api/v1/staff/notify/history", **staff_headers)
    assert resp.status_code == 200
    assert resp.json() == []


# ── Mutações de Template/Trigger: LOCAL-ONLY (o catálogo é do supletivo, sem dual-write) ──────
# Em remote, as mutações NÃO tocam o notify-server: o teor é resolvido no ENVIO (send_event) e
# entregue como `content` pronto. Qualquer chamada ao SDK numa mutação é bug → `no_http` derruba.

_BODY = {"body_md": "Oi {nome}, chegou!", "is_tts": True, "channels": "whatsapp"}
_SEED_EVENT = (
    "candidate.awaiting_approval"  # primeiro evento do notify/seed/templates.md
)


def test_put_template_remote_local_only(client, staff_headers, remote, no_http):
    """PUT em modo remote escreve local e NÃO chama o notify-server."""
    from notify.models import Template

    resp = _put(client, "/api/v1/staff/notify/templates/ev.x", _BODY, staff_headers)
    assert resp.status_code == 200
    assert Template.objects.filter(event="ev.x").exists()


def test_patch_template_remote_local_only(client, staff_headers, remote, no_http):
    from notify.models import Template

    Template.objects.create(event="ev.x", body_md="Oi {nome}", is_tts=False)
    resp = client.patch(
        "/api/v1/staff/notify/templates/ev.x",
        data=json.dumps({"is_tts": True}),
        content_type="application/json",
        **staff_headers,
    )
    assert resp.status_code == 200
    assert Template.objects.get(event="ev.x").is_tts is True


def test_put_trigger_remote_local_only(client, staff_headers, remote, no_http):
    from notify.models import Template, Trigger

    Template.objects.create(event="ev.x", body_md="Oi")
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
    tr = Trigger.objects.get(template__event="ev.x")
    assert tr.active is False
    assert tr.delay_minutes == 0  # clamp ≥0


def test_delete_template_remote_local_only(client, staff_headers, remote, no_http):
    from notify.models import Template

    Template.objects.create(event="ev.x", body_md="Oi")
    resp = client.delete("/api/v1/staff/notify/templates/ev.x", **staff_headers)
    assert resp.status_code == 200
    assert not Template.objects.filter(event="ev.x").exists()


def test_restore_seed_remote_local_only(client, staff_headers, remote, no_http):
    from notify.models import Template

    resp = client.post(
        f"/api/v1/staff/notify/templates/{_SEED_EVENT}/restore-seed", **staff_headers
    )
    assert resp.status_code == 200
    assert Template.objects.filter(event=_SEED_EVENT).exists()


def test_test_template_unknown_event_returns_404(client, staff_headers, no_http):
    """POST /templates/{event}/test de evento sem Template e fora do catálogo → 404 EVENT_NOT_FOUND
    (send_event levanta KeyError via msgs.text; o endpoint traduz pra 404 em vez de 500)."""
    resp = client.post(
        "/api/v1/staff/notify/templates/evento.que.nao.existe/test",
        data=json.dumps({}),
        content_type="application/json",
        **staff_headers,
    )
    assert resp.status_code == 404
    assert resp.json()["code"] == "EVENT_NOT_FOUND"
