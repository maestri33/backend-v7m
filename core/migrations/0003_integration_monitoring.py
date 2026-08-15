from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):
    dependencies = [("core", "0002_validationcheck")]

    operations = [
        migrations.CreateModel(
            name="IntegrationAutomationPolicy",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("integration", models.CharField(max_length=64, unique=True)),
                ("enabled", models.BooleanField(default=True)),
                ("failure_threshold", models.PositiveSmallIntegerField(default=2)),
                ("ai_triage_enabled", models.BooleanField(default=True)),
                ("notify_enabled", models.BooleanField(default=True)),
                ("auto_purchase_enabled", models.BooleanField(default=False)),
                ("requires_approval", models.BooleanField(default=True)),
                (
                    "minimum_balance",
                    models.DecimalField(
                        blank=True, decimal_places=2, max_digits=12, null=True
                    ),
                ),
                (
                    "purchase_amount",
                    models.DecimalField(
                        blank=True, decimal_places=2, max_digits=12, null=True
                    ),
                ),
                (
                    "daily_limit",
                    models.DecimalField(
                        blank=True, decimal_places=2, max_digits=12, null=True
                    ),
                ),
                ("currency", models.CharField(default="BRL", max_length=3)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
        ),
        migrations.CreateModel(
            name="IntegrationIncident",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "external_id",
                    models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
                ),
                ("integration", models.CharField(db_index=True, max_length=64)),
                ("operation", models.CharField(db_index=True, max_length=128)),
                ("fingerprint", models.CharField(db_index=True, max_length=64)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("open", "open"),
                            ("resolved", "resolved"),
                            ("suppressed", "suppressed"),
                        ],
                        default="open",
                        max_length=16,
                    ),
                ),
                (
                    "severity",
                    models.CharField(
                        choices=[
                            ("warning", "warning"),
                            ("error", "error"),
                            ("critical", "critical"),
                        ],
                        default="error",
                        max_length=16,
                    ),
                ),
                ("error_type", models.CharField(max_length=128)),
                ("error_code", models.CharField(blank=True, max_length=64)),
                ("detail", models.TextField(blank=True)),
                ("context", models.JSONField(blank=True, default=dict)),
                ("occurrences", models.PositiveIntegerField(default=1)),
                ("first_seen_at", models.DateTimeField(auto_now_add=True)),
                ("last_seen_at", models.DateTimeField(auto_now=True)),
                ("resolved_at", models.DateTimeField(blank=True, null=True)),
                ("resolution", models.TextField(blank=True)),
                (
                    "ai_status",
                    models.CharField(
                        choices=[
                            ("pending", "pending"),
                            ("success", "success"),
                            ("error", "error"),
                            ("skipped", "skipped"),
                        ],
                        default="pending",
                        max_length=16,
                    ),
                ),
                ("ai_summary", models.TextField(blank=True)),
                ("ai_recommendation", models.TextField(blank=True)),
                ("notification_external_id", models.UUIDField(blank=True, null=True)),
                ("notified_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["status", "-last_seen_at"],
                        name="core_integr_status_eafb2e_idx",
                    ),
                    models.Index(
                        fields=["integration", "operation", "-last_seen_at"],
                        name="core_integr_integra_659139_idx",
                    ),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        condition=models.Q(("status", "open")),
                        fields=("integration", "operation", "fingerprint"),
                        name="core_unique_open_integration_incident",
                    )
                ],
            },
        ),
        migrations.CreateModel(
            name="IntegrationAction",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "external_id",
                    models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
                ),
                ("integration", models.CharField(db_index=True, max_length=64)),
                (
                    "action_type",
                    models.CharField(default="minimum_purchase", max_length=32),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending_approval", "pending_approval"),
                            ("approved", "approved"),
                            ("executing", "executing"),
                            ("succeeded", "succeeded"),
                            ("failed", "failed"),
                            ("blocked", "blocked"),
                        ],
                        default="pending_approval",
                        max_length=24,
                    ),
                ),
                ("idempotency_key", models.CharField(max_length=255, unique=True)),
                ("amount", models.DecimalField(decimal_places=2, max_digits=12)),
                ("currency", models.CharField(default="BRL", max_length=3)),
                ("reason", models.TextField(blank=True)),
                ("result", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("executed_at", models.DateTimeField(blank=True, null=True)),
                (
                    "incident",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="actions",
                        to="core.integrationincident",
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["integration", "status", "-created_at"],
                        name="core_integr_integra_62a043_idx",
                    )
                ]
            },
        ),
    ]
