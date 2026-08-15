from decimal import Decimal

import pytest
from django.test import override_settings

from core.models import (
    IntegrationAction,
    IntegrationAutomationPolicy,
    IntegrationIncident,
    ValidationCheck,
)
from integrations import monitoring
from integrations.tasks import triage_incident


pytestmark = pytest.mark.django_db


def test_failure_is_sanitized_and_deduplicated(
    monkeypatch, django_capture_on_commit_callbacks
):
    queued = []
    monkeypatch.setattr("django_q.tasks.async_task", lambda *args: queued.append(args))

    with django_capture_on_commit_callbacks(execute=True):
        first = monitoring.record_failure(
            "cpf",
            "lookup",
            "Authorization: Bearer-secret para 09126367939 em victor@example.com",
            error_code="401",
            severity="critical",
        )
        second = monitoring.record_failure(
            "cpf",
            "lookup",
            "Authorization: another-secret para 09126367939 em victor@example.com",
            error_code="401",
            severity="critical",
        )

    assert first.pk == second.pk
    second.refresh_from_db()
    assert second.occurrences == 2
    assert "09126367939" not in second.detail
    assert "victor@example.com" not in second.detail
    assert "another-secret" not in second.detail
    assert monitoring.sanitize({"api_key": "secret-value"}) == {"api_key": "[redacted]"}
    assert ValidationCheck.objects.filter(scope="cpf", passed=False).count() == 2
    assert queued


def test_success_resolves_open_incident(monkeypatch):
    monkeypatch.setattr("django_q.tasks.async_task", lambda *args: None)
    incident = monitoring.record_failure("cep", "lookup", "timeout")

    monitoring.record_success("cep", "lookup", latency_ms=35)

    incident.refresh_from_db()
    assert incident.status == IntegrationIncident.Status.RESOLVED
    assert incident.resolved_at is not None
    assert ValidationCheck.objects.filter(scope="cep", passed=True).exists()


@override_settings(INTEGRATION_AUTO_ACTIONS_ENABLED=False)
def test_minimum_purchase_requires_approval_and_is_idempotent():
    IntegrationAutomationPolicy.objects.create(
        integration="cpf",
        auto_purchase_enabled=True,
        requires_approval=True,
        minimum_balance=Decimal("20.00"),
        purchase_amount=Decimal("10.00"),
        daily_limit=Decimal("10.00"),
    )

    first = monitoring.request_minimum_purchase("cpf", balance="5.00")
    second = monitoring.request_minimum_purchase("cpf", balance="4.00")

    assert first.pk == second.pk
    assert first.status == IntegrationAction.Status.PENDING_APPROVAL
    assert IntegrationAction.objects.count() == 1


@override_settings(
    INTEGRATION_AUTO_ACTIONS_ENABLED=True,
    INTEGRATION_AUTO_PURCHASE_ALLOWLIST=["cpf"],
)
def test_purchase_without_executor_is_blocked():
    IntegrationAutomationPolicy.objects.create(
        integration="cpf",
        auto_purchase_enabled=True,
        requires_approval=False,
        minimum_balance=Decimal("20.00"),
        purchase_amount=Decimal("10.00"),
    )
    action = IntegrationAction.objects.create(
        integration="cpf",
        status=IntegrationAction.Status.APPROVED,
        idempotency_key="manual-test",
        amount=Decimal("10.00"),
    )

    from integrations.tasks import execute_integration_action

    execute_integration_action(str(action.external_id))

    action.refresh_from_db()
    assert action.status == IntegrationAction.Status.BLOCKED
    assert "executor" in action.result["error"]


@override_settings(
    INTEGRATION_AUTO_ACTIONS_ENABLED=True,
    INTEGRATION_AUTO_PURCHASE_ALLOWLIST=["cpf"],
)
def test_purchase_executor_failure_is_failed(monkeypatch):
    IntegrationAutomationPolicy.objects.create(
        integration="cpf",
        auto_purchase_enabled=True,
        requires_approval=False,
        minimum_balance=Decimal("20.00"),
        purchase_amount=Decimal("10.00"),
    )
    action = IntegrationAction.objects.create(
        integration="cpf",
        status=IntegrationAction.Status.APPROVED,
        idempotency_key="executor-failure",
        amount=Decimal("10.00"),
    )
    monkeypatch.setitem(
        monitoring._EXECUTORS,
        "cpf",
        lambda **kwargs: (_ for _ in ()).throw(ConnectionError("provider down")),
    )

    from integrations.tasks import execute_integration_action

    execute_integration_action(str(action.external_id))

    action.refresh_from_db()
    assert action.status == IntegrationAction.Status.FAILED


@override_settings(
    INTEGRATION_AI_TRIAGE_ENABLED=True,
    INTEGRATION_ALERT_PHONE="5511999999999",
    INTEGRATION_ALERT_EMAIL="",
)
def test_triage_uses_ai_and_notifies_once(monkeypatch):
    incident = IntegrationIncident.objects.create(
        integration="cpf",
        operation="lookup",
        fingerprint="a" * 64,
        error_type="CpfHubError",
        error_code="429",
        detail="limite excedido",
        occurrences=2,
    )
    monkeypatch.setattr(
        "integrations.ai.service.generate_json",
        lambda *args, **kwargs: {
            "summary": "créditos ou rate limit esgotados",
            "recommended_action": "verificar saldo antes de recarregar",
        },
    )
    sent = []
    monkeypatch.setattr(
        "notify.interface.send.send",
        lambda **kwargs: sent.append(kwargs) or "36f57f3f-5ddd-494d-b971-d55a64d16c53",
    )

    triage_incident(str(incident.external_id))
    triage_incident(str(incident.external_id))

    incident.refresh_from_db()
    assert incident.ai_status == IntegrationIncident.AiStatus.SUCCESS
    assert incident.notified_at is not None
    assert len(sent) == 1
    assert sent[0]["idempotency_key"].endswith(":open")


@override_settings(
    INTEGRATION_AI_TRIAGE_ENABLED=False,
    INTEGRATION_ALERT_PHONE="5511999999999",
)
def test_notify_incident_does_not_alert_through_broken_notify(monkeypatch):
    incident = IntegrationIncident.objects.create(
        integration="notify",
        operation="POST /v1/send",
        fingerprint="b" * 64,
        error_type="ConnectError",
        detail="indisponível",
    )
    monkeypatch.setattr(
        "notify.interface.send.send",
        lambda **kwargs: pytest.fail("não deve criar loop pelo notify quebrado"),
    )

    triage_incident(str(incident.external_id))

    incident.refresh_from_db()
    assert incident.ai_status == IntegrationIncident.AiStatus.SKIPPED
    assert incident.notified_at is None
