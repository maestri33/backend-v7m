import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL)]

    operations = [
        migrations.CreateModel(
            name="NotificationTemplate",
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
                    "event",
                    models.SlugField(
                        editable=False, max_length=100, unique=True, verbose_name="evento"
                    ),
                ),
                (
                    "title",
                    models.CharField(
                        blank=True, max_length=160, null=True, verbose_name="título"
                    ),
                ),
                (
                    "subject",
                    models.CharField(
                        blank=True,
                        max_length=200,
                        null=True,
                        verbose_name="assunto do e-mail",
                    ),
                ),
                ("body", models.TextField(verbose_name="conteúdo")),
                (
                    "channels",
                    models.CharField(
                        default="whatsapp,email", max_length=40, verbose_name="canais"
                    ),
                ),
                ("is_tts", models.BooleanField(default=False, verbose_name="gerar áudio")),
                (
                    "media_url",
                    models.URLField(
                        blank=True,
                        max_length=500,
                        null=True,
                        verbose_name="URL da mídia",
                    ),
                ),
                (
                    "media_type",
                    models.CharField(
                        blank=True,
                        choices=[
                            ("", "Sem mídia"),
                            ("image", "Imagem"),
                            ("video", "Vídeo"),
                            ("audio", "Áudio"),
                            ("document", "Documento"),
                        ],
                        default="",
                        max_length=20,
                        verbose_name="tipo da mídia",
                    ),
                ),
                (
                    "mail_template",
                    models.CharField(
                        default="default", max_length=80, verbose_name="layout do e-mail"
                    ),
                ),
                ("active", models.BooleanField(default=True, verbose_name="ativo")),
                (
                    "context_keys",
                    models.JSONField(
                        default=list, editable=False, verbose_name="campos disponíveis"
                    ),
                ),
                ("default_hash", models.CharField(editable=False, max_length=64)),
                (
                    "customized_at",
                    models.DateTimeField(
                        blank=True,
                        editable=False,
                        null=True,
                        verbose_name="personalizado em",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "customized_by",
                    models.ForeignKey(
                        blank=True,
                        editable=False,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="customized_notification_templates",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="personalizado por",
                    ),
                ),
            ],
            options={
                "verbose_name": "conteúdo de notificação",
                "verbose_name_plural": "conteúdos de notificação",
                "ordering": ("event",),
            },
        )
    ]
