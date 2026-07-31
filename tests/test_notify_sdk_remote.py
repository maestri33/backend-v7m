"""Testes do modo NOTIFY_MODE=remote (Fase 2): SDK HTTP com transporte mockado.

O transporte é a `notify.sdk.client._request` (ponto único de rede) — monkeypatch, sem respx.
Cobrem: contrato de retorno do send/send_event, chave de idempotência no payload, on_commit
(§12), drop/retry do push e o `story_or_none` (storytelling do modo remote).
"""

import uuid
from types import SimpleNamespace

import pytest


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
def remote(settings):
    settings.NOTIFY_MODE = "remote"
    settings.NOTIFY_URL = "http://notify.test"
    settings.NOTIFY_INSTANCE = "supletivo"
    settings.NOTIFY_API_KEY = "test-key"
    settings.NOTIFY_TIMEOUT = 5.0
    settings.NOTIFY_SYNC_TIMEOUT = 33.0
    return settings


@pytest.fixture
def q_capture(monkeypatch):
    """Captura os async_task do Django-Q (nome + args) sem enfileirar de verdade."""
    import django_q.tasks

    calls = []
    monkeypatch.setattr(
        django_q.tasks,
        "async_task",
        lambda name, *args, **kw: calls.append((name, args)),
    )
    return calls


@pytest.fixture
def http_capture(monkeypatch):
    """Troca a `client._request` por um fake: grava a chamada e devolve a resposta enfileirada."""
    from notify.sdk import client

    state = {"calls": [], "responses": []}

    def fake_request(method, path, *, json=None, params=None, timeout=None):
        state["calls"].append(
            {
                "method": method,
                "path": path,
                "json": json,
                "params": params,
                "timeout": timeout,
            }
        )
        if not state["responses"]:
            raise AssertionError("nenhuma resposta enfileirada p/ _request")
        return state["responses"].pop(0)

    monkeypatch.setattr(client, "_request", fake_request)
    return state


# ── send() remote ────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_send_remote_devolve_uuid_e_enfileira_push(
    remote, q_capture, django_capture_on_commit_callbacks
):
    """Async: retorno imediato (uuid do cliente) e push_send enfileirado com o payload do contrato
    genérico /v1/send (conteúdo pronto em `content`, roteamento por `instance`, `sexo` p/ TTS)."""
    from notify.interface.send import send
    from notify.models import Notification

    with django_capture_on_commit_callbacks(execute=True):
        ret = send(
            text="oi maria, tudo bem?",
            caller="test.caller",
            phone="5511999990001",
            email="maria@x.com",
            title="Titulo do envio",
            subject="Assunto do envio",
            whatsapp=True,
            email_channel=True,
            tts=True,
            media_url="http://x/doc.pdf",
            media_type="document",
            gender="F",
            mail_template="custom-tpl",
        )

    uuid.UUID(ret)  # handle é uuid válido gerado no cliente
    assert len(q_capture) == 1
    name, args = q_capture[0]
    assert name == "notify.sdk.push.push_send"
    payload = args[0]
    assert payload == {
        "content": "oi maria, tudo bem?",
        "caller": "test.caller",
        "phone": "5511999990001",
        "email": "maria@x.com",
        "title": "Titulo do envio",
        "subject": "Assunto do envio",
        "whatsapp": True,
        "email_channel": True,
        "tts": True,
        "media_url": "http://x/doc.pdf",
        "media_type": "document",
        "sexo": "F",
        "instance": "supletivo",
        "external_id": ret,
        "run_sync": False,
    }
    # sem espelho local no modo remote — a fila durável é o próprio Django-Q
    assert Notification.objects.count() == 0


@pytest.mark.django_db
def test_send_remote_post_so_depois_do_commit(
    remote, q_capture, django_capture_on_commit_callbacks
):
    """§12: nada é enfileirado antes do commit; o callback do on_commit faz o enqueue."""
    from notify.interface.send import send

    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        ret = send(text="oi", caller="test", phone="5511999990001")
        assert q_capture == []
    assert len(callbacks) == 1
    callbacks[0]()
    assert q_capture[0][0] == "notify.sdk.push.push_send"
    assert q_capture[0][1][0]["external_id"] == ret


@pytest.mark.django_db
def test_send_remote_idempotency_key_do_caller_vira_chave(
    remote, q_capture, django_capture_on_commit_callbacks
):
    """A idempotency_key do caller vai no payload como external_id (chave do servidor)."""
    from notify.interface.send import send

    with django_capture_on_commit_callbacks(execute=True):
        ret = send(
            text="oi",
            caller="test",
            phone="5511999990001",
            idempotency_key="minha-chave",
        )

    uuid.UUID(ret)  # retorno é uuid novo (dangling aceito — recon R4), nunca a chave
    payload = q_capture[0][1][0]
    assert payload["external_id"] == "minha-chave"


def test_send_remote_run_sync_devolve_id_do_servidor(remote, http_capture):
    """run_sync=True: POST inline com timeout folgado, retorna o external_id da resposta."""
    from notify.interface.send import send

    http_capture["responses"].append(_FakeResp(200, {"external_id": "srv-uuid-9"}))
    ret = send(text="oi", caller="test", phone="5511999990001", run_sync=True)

    assert ret == "srv-uuid-9"
    call = http_capture["calls"][0]
    assert call["method"] == "POST"
    assert call["path"] == "/v1/send"
    assert call["json"]["run_sync"] is True
    assert call["timeout"] == 33.0  # NOTIFY_SYNC_TIMEOUT no inline


def test_send_remote_media_autodetect_local(remote, http_capture):
    """O auto-detect do media_type continua no cliente (paridade com o caminho local)."""
    from notify.interface.send import send

    http_capture["responses"].append(_FakeResp(200, {"external_id": "srv-1"}))
    send(
        text="qr",
        caller="test",
        phone="5511999990001",
        media_url="http://x/qr.png?v=1",
        run_sync=True,
    )
    assert http_capture["calls"][0]["json"]["media_type"] == "image"


# ── push tasks (retry do Django-Q) ───────────────────────────────────────────


def test_push_send_dropa_em_4xx_permanente(remote, http_capture):
    """400/404/422 = permanente: loga e retorna sem levantar (sem retry do Q)."""
    from notify.sdk import push

    for status in (400, 404, 422):
        http_capture["responses"].append(_FakeResp(status, {"detail": "ruim"}))
        push.push_send({"external_id": "x", "caller": "t"})  # não levanta
    assert len(http_capture["calls"]) == 3


def test_push_send_relevanta_em_5xx(remote, http_capture):
    """5xx re-levanta: o Django-Q re-tenta com a MESMA chave (servidor deduplica)."""
    from notify.sdk import client, push

    http_capture["responses"].append(_FakeResp(500, None, text="<html>erro</html>"))
    with pytest.raises(client.NotifyServerError):
        push.push_send({"external_id": "x", "caller": "t"})


def test_push_send_erro_de_conexao_propaga(remote, monkeypatch):
    import httpx

    from notify.sdk import client, push

    def boom(method, path, *, json=None, params=None, timeout=None):
        raise httpx.ConnectError("recusado")

    monkeypatch.setattr(client, "_request", boom)
    with pytest.raises(httpx.ConnectError):
        push.push_send({"external_id": "x", "caller": "t"})


# ── send_event() remote — teor resolvido AQUI, POST /v1/send com content pronto ──────


@pytest.mark.django_db
def test_send_event_remote_resolve_profile_e_enfileira(
    remote, q_capture, django_capture_on_commit_callbacks
):
    """Profile + teor resolvidos localmente; push_send enfileirado com o `content` JÁ pronto
    (o notify-server não conhece evento/Template) e roteado por `instance`."""
    from notify.interface import templates as _tpl
    from notify.interface.events import send_event
    from users.roles import notifications as msgs

    _tpl.invalidate(
        "lead.captured"
    )  # garante o caminho catálogo in-memory (sem row no DB)
    expected = msgs.text("lead.captured", nome="Maria", name="Maria", valor="R$ 999")

    prof = SimpleNamespace(
        name="Maria da Silva",
        phone="5511988887777",
        email="maria@x.com",
        gender="F",
        birth_date=None,
    )
    with django_capture_on_commit_callbacks(execute=True):
        ret = send_event(
            "lead.captured",
            profile=prof,
            ctx={"valor": "R$ 999"},
            title="Titulo do evento",
            subject="Assunto do evento",
            media_url="http://x/foto.png",
            media_type="image",
            mail_template="evento-tpl",
        )

    uuid.UUID(ret)
    name, args = q_capture[0]
    assert name == "notify.sdk.push.push_send"
    payload = args[0]
    assert payload == {
        "content": expected,
        "caller": "event:lead.captured",
        "phone": "5511988887777",
        "email": "maria@x.com",
        "title": "Titulo do evento",
        "subject": "Assunto do evento",
        "whatsapp": True,
        "email_channel": True,
        "tts": False,  # lead.captured não é TTS
        "media_url": "http://x/foto.png",
        "media_type": "image",
        "sexo": None,  # sexo só vai quando é voice-note (TTS)
        "instance": "supletivo",
        "external_id": ret,  # sem chave do caller → uuid do cliente
        "run_sync": False,
    }


@pytest.mark.django_db
def test_send_event_remote_overrides_aplicados_localmente(
    remote, q_capture, django_capture_on_commit_callbacks
):
    """is_tts_override/channels_override são aplicados AQUI (viram tts/whatsapp no payload pronto);
    a idempotency_key do caller vira o external_id (chave do servidor)."""
    from notify.interface import templates as _tpl
    from notify.interface.events import send_event

    _tpl.invalidate("candidate.document_in_review")
    with django_capture_on_commit_callbacks(execute=True):
        ret = send_event(
            "candidate.document_in_review",
            phone="5511987654321",
            idempotency_key="chave-estavel",
            is_tts_override=True,
            channels_override=("whatsapp",),
        )

    uuid.UUID(ret)
    payload = q_capture[0][1][0]
    assert payload["external_id"] == "chave-estavel"
    assert payload["whatsapp"] is True
    assert (
        payload["email_channel"] is False
    )  # channels_override=("whatsapp",) tira o e-mail
    assert payload["tts"] is True  # is_tts_override=True → voice-note


def test_send_event_remote_sem_destinatario_vira_none(remote, monkeypatch):
    """Pré-check local: sem phone nem email não há o que despachar — None sem HTTP."""
    from notify.interface.events import send_event
    from notify.sdk import client

    monkeypatch.setattr(
        client, "_request", lambda *a, **k: pytest.fail("não deveria chamar HTTP")
    )
    assert send_event("lead.captured") is None


@pytest.mark.django_db
def test_send_event_remote_sync_devolve_id_do_servidor(remote, http_capture):
    from notify.interface import templates as _tpl
    from notify.interface.events import send_event

    _tpl.invalidate("lead.captured")
    http_capture["responses"].append(_FakeResp(200, {"external_id": "srv-77"}))
    ret = send_event("lead.captured", phone="5511999990001", run_sync=True)

    assert ret == "srv-77"
    call = http_capture["calls"][0]
    assert call["path"] == "/v1/send"  # não há mais /v1/send-event
    assert call["json"]["run_sync"] is True
    assert call["timeout"] == 33.0


@pytest.mark.django_db
def test_send_event_remote_story_vira_content(
    remote, q_capture, monkeypatch, django_capture_on_commit_callbacks
):
    """Storytelling fica no backend: o texto da IA vira o `content` pronto do POST /v1/send."""
    from integrations.ai import service as ai
    from notify.interface import templates as _tpl
    from notify.interface.events import send_event

    # sem row no DB p/ este evento (o cache de Template pode estar sujo de outro teste)
    _tpl.invalidate("enrollment.selfie_approved")
    story = "Maria, hoje você assinou a sua matrícula com o próprio rosto. Parabéns!"
    monkeypatch.setattr(ai, "generate_text", lambda *a, **k: story)
    prof = SimpleNamespace(
        name="Maria da Silva",
        phone="5511988887777",
        email=None,
        gender="F",
        birth_date=None,
    )
    with django_capture_on_commit_callbacks(execute=True):
        send_event("enrollment.selfie_approved", profile=prof)

    assert q_capture[0][1][0]["content"] == story


@pytest.mark.django_db
def test_send_event_remote_override_do_caller_tem_precedencia(
    remote, q_capture, monkeypatch, django_capture_on_commit_callbacks
):
    """body_md_override do caller vira o `content` sem gerar story por cima."""
    from notify.interface.events import send_event
    from users.roles import notifications as msgs

    monkeypatch.setattr(
        msgs,
        "story_text",
        lambda *a, **k: pytest.fail("não gera story por cima do override do caller"),
    )
    with django_capture_on_commit_callbacks(execute=True):
        send_event(
            "enrollment.selfie_approved",
            phone="5511999990001",
            body_md_override="texto do caller",
        )
    assert q_capture[0][1][0]["content"] == "texto do caller"


# ── story_or_none ────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_story_or_none_evento_sem_story_vira_none():
    from notify.interface import templates as _tpl
    from users.roles import notifications as msgs

    _tpl.invalidate("lead.captured")
    assert msgs.story_or_none("lead.captured", name="Maria") is None


@pytest.mark.django_db
def test_story_or_none_falha_de_ia_vira_none(monkeypatch):
    from integrations.ai import service as ai
    from notify.interface import templates as _tpl
    from users.roles import notifications as msgs

    _tpl.invalidate("enrollment.selfie_approved")

    def boom(*a, **k):
        raise RuntimeError("IA fora do ar")

    monkeypatch.setattr(ai, "generate_text", boom)
    assert (
        msgs.story_or_none("enrollment.selfie_approved", name="Maria", age=40) is None
    )


@pytest.mark.django_db
def test_story_or_none_texto_ruim_vira_none(monkeypatch):
    """Guardas do story_text (len>=20, nome citado) valem: texto ruim → None."""
    from integrations.ai import service as ai
    from notify.interface import templates as _tpl
    from users.roles import notifications as msgs

    _tpl.invalidate("enrollment.selfie_approved")
    monkeypatch.setattr(ai, "generate_text", lambda *a, **k: "curto")
    assert msgs.story_or_none("enrollment.selfie_approved", name="Maria") is None


@pytest.mark.django_db
def test_story_or_none_desligado_no_template_vira_none(monkeypatch):
    """Template.storytelling=False desliga o story sem chamar a IA."""
    from integrations.ai import service as ai
    from notify.models import Template
    from users.roles import notifications as msgs

    monkeypatch.setattr(
        ai, "generate_text", lambda *a, **k: pytest.fail("IA não deveria rodar")
    )
    Template.objects.create(
        event="enrollment.selfie_approved", body_md="Oi {name}", storytelling=False
    )
    assert msgs.story_or_none("enrollment.selfie_approved", name="Maria") is None


@pytest.mark.django_db
def test_story_or_none_gera_texto_quando_ligado(monkeypatch):
    from integrations.ai import service as ai
    from notify.interface import templates as _tpl
    from users.roles import notifications as msgs

    _tpl.invalidate("student.diploma_issued")
    story = "Maria, o seu diploma está pronto — e essa conquista é sua para sempre."
    monkeypatch.setattr(ai, "generate_text", lambda *a, **k: story)
    assert msgs.story_or_none("student.diploma_issued", name="Maria", age=55) == story


# ── regressões pegas pelo review adversarial (rodada de fechamento) ────────────


@pytest.mark.django_db
def test_local_replay_da_idempotency_key_ainda_honra_trigger_active():
    """local_idempotent_hit só pode rodar no ramo remote de send_event — em modo LOCAL,
    Trigger.active precisa ser reavaliado a CADA chamada, inclusive numa chave repetida
    (o kill-switch "sem código" tem que valer também pra replay). Achado do review: uma
    versão anterior do fix rodava o pré-check antes da checagem de Trigger em qualquer modo,
    devolvendo o external_id antigo em vez de None quando o staff desligava o evento depois."""
    from notify.interface.events import send_event
    from notify.models import Notification, Template, Trigger

    template = Template.objects.create(
        event="test.trigger_replay", body_md="oi {nome}", channels="whatsapp"
    )
    trigger = Trigger.objects.create(template=template, active=True)

    key = "replay-key-trigger-active"
    first = send_event(
        "test.trigger_replay", phone="5511999990000", idempotency_key=key, run_sync=True
    )
    assert first is not None
    assert Notification.objects.filter(idempotency_key=key).count() == 1

    trigger.active = False
    trigger.save(update_fields=["active"])

    second = send_event(
        "test.trigger_replay", phone="5511999990000", idempotency_key=key, run_sync=True
    )
    assert second is None  # Trigger inativo vence, mesmo com a chave já usada antes
    assert (
        Notification.objects.filter(idempotency_key=key).count() == 1
    )  # nada novo criado


@pytest.mark.django_db
def test_notify_send_command_modo_remote_consulta_o_sdk(remote, http_capture):
    """CLI notify_send em modo remote: não existe row local (achado do review — o comando
    quebrava com Notification.DoesNotExist). Consulta GET /v1/notifications/{id} pelo SDK.

    run_sync=True dispara: 1º _request é o POST /v1/send, 2º é o GET de consulta — na ordem
    que o próprio comando faz as chamadas (fila FIFO do http_capture)."""
    from io import StringIO

    from django.core.management import call_command

    server_id = str(uuid.uuid4())
    http_capture["responses"].append(_FakeResp(200, {"external_id": server_id}))
    http_capture["responses"].append(
        _FakeResp(200, {"external_id": server_id, "whatsapp_status": "sent"})
    )

    out = StringIO()
    call_command(
        "notify_send", phone="5511999990000", text="oi", stdout=out, stderr=StringIO()
    )

    assert http_capture["calls"][0]["method"] == "POST"
    assert http_capture["calls"][0]["path"] == "/v1/send"
    assert http_capture["calls"][1]["method"] == "GET"
    assert http_capture["calls"][1]["path"] == f"/v1/notifications/{server_id}"
    assert server_id in out.getvalue()
    assert "sent" in out.getvalue()


@pytest.mark.django_db
def test_body_md_override_ainda_honra_trigger_inativo():
    """body_md_override NÃO fura o kill-switch: Trigger.active=False desliga o evento mesmo com o
    caller passando o corpo JÁ pronto (garantia documentada em events.py — regressão do review)."""
    from notify.interface import templates as _tpl
    from notify.interface.events import send_event
    from notify.models import Notification, Template, Trigger

    _tpl.invalidate("test.kill_override")
    t = Template.objects.create(
        event="test.kill_override", body_md="Oi {nome}", channels="whatsapp"
    )
    Trigger.objects.create(template=t, active=False)

    ret = send_event(
        "test.kill_override",
        phone="5511999990000",
        body_md_override="corpo pronto do caller",
        run_sync=True,
    )
    assert ret is None
    assert Notification.objects.filter(caller="event:test.kill_override").count() == 0
