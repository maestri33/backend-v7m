from __future__ import annotations

import uuid

import pytest


class Response:
    def __init__(self, status=200, data=None):
        self.status_code = status
        self._data = data or {}
        self.text = str(self._data)

    def json(self):
        return self._data


@pytest.fixture(autouse=True)
def notify_settings(settings):
    settings.NOTIFY_SERVER_URL = "http://notify.test"
    settings.NOTIFY_API_KEY = "secret"
    settings.NOTIFY_TIMEOUT = 5
    settings.NOTIFY_SYNC_TIMEOUT = 30


def test_sdk_send(monkeypatch):
    from integrations.notify import client

    seen = {}

    def request(method, path, **kwargs):
        seen.update(method=method, path=path, **kwargs)
        return Response(data={"external_id": "remote-id"})

    monkeypatch.setattr(client, "_request", request)
    assert client.post_send({"text": "oi"}, run_sync=True)["external_id"] == "remote-id"
    assert (seen["method"], seen["path"]) == ("POST", "/v1/send")


def test_send_async_enfileira_payload_estavel(monkeypatch):
    from django.db import transaction
    from django_q import tasks
    from integrations.notify.delivery import send

    queued = []
    monkeypatch.setattr(transaction, "on_commit", lambda fn: fn())
    monkeypatch.setattr(tasks, "async_task", lambda *args: queued.append(args))
    result = send(
        text="oi",
        caller="test",
        phone="5511999999999",
        idempotency_key="business-key",
    )
    assert result == "business-key"
    task, payload = queued[0]
    assert task == "integrations.notify.tasks.push_send"
    assert payload["external_id"] == "business-key"


@pytest.mark.django_db
def test_send_event_resolve_profile(monkeypatch):
    from django.db import transaction
    from django_q import tasks
    from notifications import send_event
    from notifications.seed import seed_notification_templates
    from users.auth.models import User
    from users.profiles.models import Profile

    user = User.objects.create_user()
    profile = Profile.objects.create(
        user=user, name="Maria da Silva", phone="5511999999999", gender="F"
    )
    queued = []
    monkeypatch.setattr(transaction, "on_commit", lambda fn: fn())
    monkeypatch.setattr(tasks, "async_task", lambda *args: queued.append(args))
    seed_notification_templates()

    send_event("lead.paid", profile=profile, ctx={"valor": "10"})

    task, payload = queued[0]
    assert task == "integrations.notify.tasks.push_send"
    assert payload["caller"] == "lead.paid"
    assert "Maria" in payload["text"]
    assert "event" not in payload
    assert "ctx" not in payload


def test_integracao_nao_expoe_send_event():
    from integrations.notify import client

    assert not hasattr(client, "post_send_event")


def test_sdk_erro_explicito(monkeypatch):
    from integrations.notify import client

    monkeypatch.setattr(
        client, "_request", lambda *a, **k: Response(status=503, data={"detail": "down"})
    )
    with pytest.raises(client.NotifyServerError) as caught:
        client.post_send({"text": "oi"})
    assert caught.value.status_code == 503


def test_push_send_forwards_payload(monkeypatch):
    from integrations.notify import tasks

    seen = []
    monkeypatch.setattr(
        tasks.client,
        "post_send",
        lambda payload: seen.append(payload) or {"external_id": "server-id"},
    )

    tasks.push_send({"external_id": "business-key", "caller": "test"})

    assert seen == [{"external_id": "business-key", "caller": "test"}]


def test_push_send_drops_permanent_error_but_retries_transient(monkeypatch):
    from integrations.notify import client, tasks

    monkeypatch.setattr(
        tasks.client,
        "post_send",
        lambda payload: (_ for _ in ()).throw(client.NotifyServerError(422, {})),
    )
    tasks.push_send({"external_id": "invalid", "caller": "test"})

    monkeypatch.setattr(
        tasks.client,
        "post_send",
        lambda payload: (_ for _ in ()).throw(client.NotifyServerError(503, {})),
    )
    with pytest.raises(client.NotifyServerError):
        tasks.push_send({"external_id": "retry", "caller": "test"})
