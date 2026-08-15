from __future__ import annotations

import structlog
from django.conf import settings
from django.utils import timezone

from core.models import (
    IntegrationAction,
    IntegrationAutomationPolicy,
    IntegrationIncident,
)
from integrations import monitoring

logger = structlog.get_logger()


def _triage_with_ai(incident: IntegrationIncident) -> None:
    policy = IntegrationAutomationPolicy.objects.filter(
        integration=incident.integration
    ).first()
    enabled = (
        policy.ai_triage_enabled if policy else settings.INTEGRATION_AI_TRIAGE_ENABLED
    )
    if not enabled or incident.integration == "ai":
        incident.ai_status = IntegrationIncident.AiStatus.SKIPPED
        incident.save(update_fields=["ai_status"])
        return
    try:
        from integrations.ai import service as ai

        data = ai.generate_json(
            (
                "Analise este incidente de integração sem inventar fatos. "
                "Responda com summary, likely_cause, recommended_action, needs_human e urgency.\n"
                f"integration={incident.integration}\noperation={incident.operation}\n"
                f"error_type={incident.error_type}\nerror_code={incident.error_code}\n"
                f"detail={incident.detail}\ncontext={incident.context}\n"
                f"occurrences={incident.occurrences}"
            ),
            caller="integrations.incident_triage",
            schema_description=(
                "Objeto JSON: summary string, likely_cause string, recommended_action string, "
                "needs_human boolean, urgency low|medium|high|critical"
            ),
            temperature=0.1,
            max_tokens=500,
        )
        incident.ai_status = IntegrationIncident.AiStatus.SUCCESS
        incident.ai_summary = str(
            data.get("summary") or data.get("likely_cause") or ""
        )[:2000]
        incident.ai_recommendation = str(data.get("recommended_action") or "")[:2000]
    except Exception as exc:
        incident.ai_status = IntegrationIncident.AiStatus.ERROR
        incident.ai_summary = ""
        incident.ai_recommendation = (
            f"triagem por IA indisponível: {type(exc).__name__}"
        )
    incident.save(update_fields=["ai_status", "ai_summary", "ai_recommendation"])


def _notify(incident: IntegrationIncident) -> None:
    policy = IntegrationAutomationPolicy.objects.filter(
        integration=incident.integration
    ).first()
    enabled = policy.notify_enabled if policy else True
    phone = settings.INTEGRATION_ALERT_PHONE
    email = settings.INTEGRATION_ALERT_EMAIL
    if (
        not enabled
        or incident.integration == "notify"
        or not (phone or email)
        or incident.notified_at
    ):
        return
    from notify.interface.send import send

    message = (
        f"🚨 Integração {incident.integration} com falha\n"
        f"Operação: {incident.operation}\nOcorrências: {incident.occurrences}\n"
        f"Erro: {incident.error_type} {incident.error_code}\n{incident.detail[:500]}"
    )
    if incident.ai_summary:
        message += f"\n\nTriagem IA: {incident.ai_summary[:500]}"
    if incident.ai_recommendation:
        message += f"\nAção sugerida: {incident.ai_recommendation[:500]}"
    external_id = send(
        text=message,
        caller="integrations.monitor",
        phone=phone or None,
        email=email or None,
        subject=f"Falha na integração {incident.integration}",
        whatsapp=bool(phone),
        email_channel=bool(email),
        idempotency_key=f"integration-incident:{incident.external_id}:open",
    )
    incident.notification_external_id = external_id
    incident.notified_at = timezone.now()
    incident.save(update_fields=["notification_external_id", "notified_at"])


def triage_incident(external_id: str) -> None:
    incident = IntegrationIncident.objects.filter(
        external_id=external_id, status=IntegrationIncident.Status.OPEN
    ).first()
    if not incident:
        return
    _triage_with_ai(incident)
    try:
        _notify(incident)
    except Exception as exc:
        logger.warning(
            "integration.incident_notify_failed",
            integration=incident.integration,
            error=type(exc).__name__,
        )


def execute_integration_action(external_id: str) -> None:
    action = IntegrationAction.objects.filter(external_id=external_id).first()
    if not action or action.status != IntegrationAction.Status.APPROVED:
        return
    action.status = IntegrationAction.Status.EXECUTING
    action.save(update_fields=["status", "updated_at"])
    try:
        action.result = monitoring.execute_purchase(action)
        action.status = IntegrationAction.Status.SUCCEEDED
    except monitoring.ActionBlocked as exc:
        action.result = {"error": str(monitoring.sanitize(exc))}
        action.status = IntegrationAction.Status.BLOCKED
    except Exception as exc:
        action.result = {"error": str(monitoring.sanitize(exc))}
        action.status = IntegrationAction.Status.FAILED
    action.executed_at = timezone.now()
    action.save(update_fields=["status", "result", "executed_at", "updated_at"])
