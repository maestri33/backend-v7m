from __future__ import annotations

import hashlib
import re
from decimal import Decimal

import structlog
from asgiref.sync import sync_to_async
from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import F, Sum
from django.utils import timezone

from core.models import (
    IntegrationAction,
    IntegrationAutomationPolicy,
    IntegrationIncident,
)
from core.validation import record_check

logger = structlog.get_logger()

_SECRET_RE = re.compile(
    r"(?i)(authorization|api[-_ ]?key|token|secret|password)\s*[:=]\s*[^\s,;]+"
)
_EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)
_LONG_DIGITS_RE = re.compile(r"(?<!\d)\d{7,}(?!\d)")
_EXECUTORS: dict[str, object] = {}


class ActionBlocked(RuntimeError):
    pass


def sanitize(value, *, depth: int = 0):
    if depth > 4:
        return "[truncated]"
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            safe_key = str(key)[:80]
            if re.search(
                r"(?i)authorization|api[-_ ]?key|token|secret|password", safe_key
            ):
                cleaned[safe_key] = "[redacted]"
            else:
                cleaned[safe_key] = sanitize(item, depth=depth + 1)
        return cleaned
    if isinstance(value, (list, tuple)):
        return [sanitize(v, depth=depth + 1) for v in value[:20]]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    text = str(value)[:2000]
    text = _SECRET_RE.sub(r"\1=[redacted]", text)
    text = _EMAIL_RE.sub("[email]", text)
    return _LONG_DIGITS_RE.sub("[number]", text)


def _fingerprint(error_type: str, error_code: str, detail: str) -> str:
    normalized = re.sub(r"\d+", "#", detail.lower())
    raw = f"{error_type}|{error_code}|{normalized[:500]}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _policy(integration: str):
    return IntegrationAutomationPolicy.objects.filter(integration=integration).first()


def _threshold(integration: str) -> int:
    policy = _policy(integration)
    return max(
        1,
        policy.failure_threshold if policy else settings.INTEGRATION_FAILURE_THRESHOLD,
    )


def record_success(
    integration: str,
    operation: str,
    *,
    latency_ms: int | None = None,
    detail: str = "ok",
) -> None:
    safe_detail = sanitize(detail)
    if latency_ms is not None:
        safe_detail = f"{safe_detail}; latency_ms={max(0, int(latency_ms))}"
    try:
        record_check(integration, operation, True, mode="real", detail=safe_detail)
        now = timezone.now()
        IntegrationIncident.objects.filter(
            integration=integration,
            operation=operation,
            status=IntegrationIncident.Status.OPEN,
        ).update(
            status=IntegrationIncident.Status.RESOLVED,
            resolved_at=now,
            resolution="recuperação observada automaticamente",
        )
    except Exception as exc:
        logger.warning("integration.monitor_success_failed", error=type(exc).__name__)


def record_failure(
    integration: str,
    operation: str,
    error: Exception | str,
    *,
    error_code: str = "",
    severity: str = IntegrationIncident.Severity.ERROR,
    context: dict | None = None,
) -> IntegrationIncident | None:
    error_type = (
        type(error).__name__ if isinstance(error, Exception) else "ExternalError"
    )
    detail = str(sanitize(error))
    safe_context = sanitize(context or {})
    fingerprint = _fingerprint(error_type, error_code, detail)
    try:
        record_check(
            integration,
            operation,
            False,
            mode="real",
            detail=f"{error_type}:{error_code or '-'} {detail}"[:2000],
        )
        with transaction.atomic():
            incident = (
                IntegrationIncident.objects.select_for_update()
                .filter(
                    integration=integration,
                    operation=operation,
                    fingerprint=fingerprint,
                    status=IntegrationIncident.Status.OPEN,
                )
                .first()
            )
            if incident:
                IntegrationIncident.objects.filter(pk=incident.pk).update(
                    occurrences=F("occurrences") + 1,
                    severity=severity,
                    detail=detail,
                    context=safe_context,
                )
                incident.refresh_from_db()
            else:
                incident = IntegrationIncident.objects.create(
                    integration=integration,
                    operation=operation,
                    fingerprint=fingerprint,
                    severity=severity,
                    error_type=error_type,
                    error_code=error_code,
                    detail=detail,
                    context=safe_context,
                )
        policy = _policy(integration)
        enabled = policy.enabled if policy else True
        if enabled and (
            incident.occurrences == _threshold(integration)
            or severity == IntegrationIncident.Severity.CRITICAL
        ):
            from django_q.tasks import async_task

            transaction.on_commit(
                lambda: async_task(
                    "integrations.tasks.triage_incident", str(incident.external_id)
                )
            )
        return incident
    except IntegrityError:
        return IntegrationIncident.objects.filter(
            integration=integration,
            operation=operation,
            fingerprint=fingerprint,
            status=IntegrationIncident.Status.OPEN,
        ).first()
    except Exception as exc:
        logger.warning("integration.monitor_failure_failed", error=type(exc).__name__)
        return None


record_success_async = sync_to_async(record_success, thread_sensitive=True)
record_failure_async = sync_to_async(record_failure, thread_sensitive=True)


def register_purchase_executor(integration: str, executor) -> None:
    _EXECUTORS[integration] = executor


def request_minimum_purchase(
    integration: str,
    *,
    balance,
    incident: IntegrationIncident | None = None,
    reason: str = "saldo abaixo do mínimo",
) -> IntegrationAction | None:
    policy = _policy(integration)
    if not policy or not policy.auto_purchase_enabled:
        return None
    balance = Decimal(str(balance))
    if policy.minimum_balance is None or balance >= policy.minimum_balance:
        return None
    if not policy.purchase_amount or policy.purchase_amount <= 0:
        return None
    today = timezone.localdate()
    key = f"minimum-purchase:{integration}:{today.isoformat()}:{policy.currency}"
    status = (
        IntegrationAction.Status.PENDING_APPROVAL
        if policy.requires_approval
        else IntegrationAction.Status.APPROVED
    )
    action, created = IntegrationAction.objects.get_or_create(
        idempotency_key=key,
        defaults={
            "integration": integration,
            "incident": incident,
            "status": status,
            "amount": policy.purchase_amount,
            "currency": policy.currency,
            "reason": sanitize(reason),
        },
    )
    if not created:
        return action
    if not policy.requires_approval and settings.INTEGRATION_AUTO_ACTIONS_ENABLED:
        from django_q.tasks import async_task

        transaction.on_commit(
            lambda: async_task(
                "integrations.tasks.execute_integration_action", str(action.external_id)
            )
        )
    return action


def execute_purchase(action: IntegrationAction) -> dict:
    policy = _policy(action.integration)
    if not settings.INTEGRATION_AUTO_ACTIONS_ENABLED:
        raise ActionBlocked("kill-switch global de ações automáticas está desligado")
    if not policy or not policy.auto_purchase_enabled:
        raise ActionBlocked("compra automática não habilitada para esta integração")
    allowlist = set(settings.INTEGRATION_AUTO_PURCHASE_ALLOWLIST)
    if action.integration not in allowlist:
        raise ActionBlocked("integração fora da allowlist de compra automática")
    if policy.daily_limit is not None:
        spent = IntegrationAction.objects.filter(
            integration=action.integration,
            status__in=[
                IntegrationAction.Status.APPROVED,
                IntegrationAction.Status.EXECUTING,
                IntegrationAction.Status.SUCCEEDED,
            ],
            created_at__date=timezone.localdate(),
        ).exclude(pk=action.pk).aggregate(total=Sum("amount"))["total"] or Decimal("0")
        if spent + action.amount > policy.daily_limit:
            raise ActionBlocked("limite diário de compra seria excedido")
    executor = _EXECUTORS.get(action.integration)
    if executor is None:
        raise ActionBlocked("provedor ainda não possui executor de compra registrado")
    result = executor(
        amount=action.amount,
        currency=action.currency,
        idempotency_key=action.idempotency_key,
    )
    return sanitize(result if isinstance(result, dict) else {"result": result})


def incident_dict(incident: IntegrationIncident) -> dict:
    return {
        "external_id": str(incident.external_id),
        "integration": incident.integration,
        "operation": incident.operation,
        "status": incident.status,
        "severity": incident.severity,
        "occurrences": incident.occurrences,
        "error_type": incident.error_type,
        "error_code": incident.error_code,
        "detail": incident.detail,
        "context": incident.context,
        "ai_status": incident.ai_status,
        "ai_summary": incident.ai_summary,
        "ai_recommendation": incident.ai_recommendation,
        "notified_at": incident.notified_at.isoformat()
        if incident.notified_at
        else None,
        "first_seen_at": incident.first_seen_at.isoformat(),
        "last_seen_at": incident.last_seen_at.isoformat(),
        "resolved_at": incident.resolved_at.isoformat()
        if incident.resolved_at
        else None,
        "resolution": incident.resolution,
    }


def action_dict(action: IntegrationAction) -> dict:
    return {
        "external_id": str(action.external_id),
        "integration": action.integration,
        "action_type": action.action_type,
        "status": action.status,
        "amount": str(action.amount),
        "currency": action.currency,
        "reason": action.reason,
        "result": action.result,
        "incident_external_id": str(action.incident.external_id)
        if action.incident
        else None,
        "created_at": action.created_at.isoformat(),
        "executed_at": action.executed_at.isoformat() if action.executed_at else None,
    }
