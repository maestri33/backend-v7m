"""Models base comuns do core (CONVENTION §2)."""

import uuid

from django.db import models
from django.db.models import Q


class ExternalIdModel(models.Model):
    """Base abstrata com o ÚNICO external_id de borda do projeto (CONVENTION §4).

    Todo model exposto na API herda daqui em vez de redeclarar o campo. `external_id` (UUID, imutável) é
    o id opaco da borda — nunca a PK. As relações INTERNAS continuam por FK de verdade; este campo só
    aparece na fronteira da API. Como é abstrato, cada filho ganha sua própria coluna idêntica — então
    quem já declarava o campo igual NÃO muda de schema.
    """

    external_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)

    class Meta:
        abstract = True


class UnroutedEvent(models.Model):
    """Evento que chegou validado mas não tinha consumidor real ainda.

    Fallback rastreável (pedido do Victor): quando um webhook/evento é destinado a um serviço que
    ainda não existe (fees/commissions etc.), gravamos aqui + logamos, em vez de descartar em
    silêncio. Permite auditar e reprocessar depois que o app destino existir.
    """

    source = models.CharField(max_length=64, db_index=True)  # ex.: "asaas"
    event = models.CharField(max_length=255, db_index=True)
    reason = models.CharField(
        max_length=255
    )  # por que não roteou (ex.: no_matching_charge)
    payload = models.JSONField(default=dict)
    received_at = models.DateTimeField(auto_now_add=True, db_index=True)
    resolved = models.BooleanField(default=False, db_index=True)

    def __str__(self):
        return f"{self.source}:{self.event} @ {self.received_at:%Y-%m-%d %H:%M:%S}"


class ValidationCheck(models.Model):
    """Registro persistente de teste/validação que a gente fez, com flag + horário.

    Pedido do Victor: todo teste que rodarmos fica salvo, com a respectiva flag, pra **rastrear no
    futuro** se algo der errado. Append-only (cada execução grava uma linha = histórico); o `/status/`
    de cada integração mostra o ÚLTIMO resultado por `(scope, name)`. Ex.: `scope=asaas`,
    `name=webhook_external`, `passed=True`, `mode=artificial` (testado via link externo, ainda não por
    evento real do Asaas).
    """

    scope = models.CharField(max_length=64, db_index=True)  # ex.: asaas
    name = models.CharField(max_length=128, db_index=True)  # ex.: webhook_external
    passed = models.BooleanField()
    mode = models.CharField(max_length=32, blank=True)  # artificial | real | link | ...
    detail = models.TextField(blank=True)
    checked_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        indexes = [models.Index(fields=["scope", "name", "-checked_at"])]

    def __str__(self):
        flag = "OK" if self.passed else "FAIL"
        return f"{self.scope}:{self.name}={flag} @ {self.checked_at:%Y-%m-%d %H:%M}"


class IntegrationIncident(ExternalIdModel):
    """Falha externa deduplicada, pronta para triagem, alerta e resolução auditável."""

    class Status(models.TextChoices):
        OPEN = "open", "open"
        RESOLVED = "resolved", "resolved"
        SUPPRESSED = "suppressed", "suppressed"

    class Severity(models.TextChoices):
        WARNING = "warning", "warning"
        ERROR = "error", "error"
        CRITICAL = "critical", "critical"

    class AiStatus(models.TextChoices):
        PENDING = "pending", "pending"
        SUCCESS = "success", "success"
        ERROR = "error", "error"
        SKIPPED = "skipped", "skipped"

    integration = models.CharField(max_length=64, db_index=True)
    operation = models.CharField(max_length=128, db_index=True)
    fingerprint = models.CharField(max_length=64, db_index=True)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.OPEN
    )
    severity = models.CharField(
        max_length=16, choices=Severity.choices, default=Severity.ERROR
    )
    error_type = models.CharField(max_length=128)
    error_code = models.CharField(max_length=64, blank=True)
    detail = models.TextField(blank=True)
    context = models.JSONField(default=dict, blank=True)
    occurrences = models.PositiveIntegerField(default=1)
    first_seen_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(auto_now=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolution = models.TextField(blank=True)
    ai_status = models.CharField(
        max_length=16, choices=AiStatus.choices, default=AiStatus.PENDING
    )
    ai_summary = models.TextField(blank=True)
    ai_recommendation = models.TextField(blank=True)
    notification_external_id = models.UUIDField(null=True, blank=True)
    notified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["status", "-last_seen_at"]),
            models.Index(fields=["integration", "operation", "-last_seen_at"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["integration", "operation", "fingerprint"],
                condition=Q(status="open"),
                name="core_unique_open_integration_incident",
            )
        ]


class IntegrationAutomationPolicy(models.Model):
    """Guardrails operacionais e financeiros por integração."""

    integration = models.CharField(max_length=64, unique=True)
    enabled = models.BooleanField(default=True)
    failure_threshold = models.PositiveSmallIntegerField(default=2)
    ai_triage_enabled = models.BooleanField(default=True)
    notify_enabled = models.BooleanField(default=True)
    auto_purchase_enabled = models.BooleanField(default=False)
    requires_approval = models.BooleanField(default=True)
    minimum_balance = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    purchase_amount = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    daily_limit = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    currency = models.CharField(max_length=3, default="BRL")
    updated_at = models.DateTimeField(auto_now=True)


class IntegrationAction(ExternalIdModel):
    """Ação corretiva proposta/executada; compra nunca ocorre sem política e executor."""

    class Status(models.TextChoices):
        PENDING_APPROVAL = "pending_approval", "pending_approval"
        APPROVED = "approved", "approved"
        EXECUTING = "executing", "executing"
        SUCCEEDED = "succeeded", "succeeded"
        FAILED = "failed", "failed"
        BLOCKED = "blocked", "blocked"

    integration = models.CharField(max_length=64, db_index=True)
    action_type = models.CharField(max_length=32, default="minimum_purchase")
    status = models.CharField(
        max_length=24, choices=Status.choices, default=Status.PENDING_APPROVAL
    )
    incident = models.ForeignKey(
        IntegrationIncident,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="actions",
    )
    idempotency_key = models.CharField(max_length=255, unique=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3, default="BRL")
    reason = models.TextField(blank=True)
    result = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    executed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["integration", "status", "-created_at"])]
