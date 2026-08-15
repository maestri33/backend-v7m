from __future__ import annotations

import re
from ipaddress import ip_address
from string import Formatter
from urllib.parse import urlsplit

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models


CHANNELS = frozenset({"whatsapp", "email"})
_PLACEHOLDER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")
MEDIA_TYPES = (
    ("", "Sem mídia"),
    ("image", "Imagem"),
    ("video", "Vídeo"),
    ("audio", "Áudio"),
    ("document", "Documento"),
)
EVENT_VALIDATOR = RegexValidator(
    regex=r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$",
    message="Use apenas letras minúsculas, números, ponto, hífen ou underscore.",
)
STORY_PROMPT_KEYS = frozenset({"name", "nome", "data_hoje", "faixa_etaria"})


def parse_channels(value: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(part.strip().lower() for part in value.split(",") if part.strip()))


def placeholders(*values: str | None) -> set[str]:
    found: set[str] = set()
    for value in values:
        try:
            parsed = Formatter().parse(value or "")
            for _, field_name, format_spec, conversion in parsed:
                if not field_name:
                    continue
                if not _PLACEHOLDER_RE.fullmatch(field_name):
                    raise ValidationError(
                        f"Campo de conteúdo inválido ou inseguro: {field_name}."
                    )
                if format_spec or conversion:
                    raise ValidationError(
                        f"Formatação ou conversão não permitida no campo: {field_name}."
                    )
                found.add(field_name)
        except ValueError as exc:
            raise ValidationError(f"Conteúdo com chaves malformadas: {exc}.") from exc
    return found


def validate_media_url(value: str | None) -> None:
    if not value:
        return
    parsed = urlsplit(value)
    host = (parsed.hostname or "").lower().rstrip(".")
    allowed_hosts = {
        item.strip().lower().rstrip(".")
        for item in getattr(settings, "NOTIFICATION_MEDIA_ALLOWED_HOSTS", [])
        if item.strip()
    }
    if parsed.scheme not in {"http", "https"} or not host or parsed.username or parsed.password:
        raise ValidationError({"media_url": "Informe uma URL HTTP(S) sem credenciais."})
    try:
        address = ip_address(host)
    except ValueError:
        address = None
    if address and not address.is_global:
        raise ValidationError({"media_url": "Endereços internos não são permitidos."})
    if host not in allowed_hosts:
        raise ValidationError(
            {"media_url": "O host da mídia não está autorizado na configuração."}
        )


class NotificationTemplate(models.Model):
    event = models.CharField(
        "evento", max_length=100, unique=True, editable=False, validators=[EVENT_VALIDATOR]
    )
    title = models.CharField("título", max_length=160, null=True, blank=True)
    subject = models.CharField("assunto do e-mail", max_length=200, null=True, blank=True)
    body = models.TextField("conteúdo")
    channels = models.CharField("canais", max_length=40, default="whatsapp,email")
    is_tts = models.BooleanField("gerar áudio", default=False)
    storytelling = models.BooleanField("personalizar marco com IA", default=False)
    story_prompt = models.TextField("instrução da história", null=True, blank=True)
    media_url = models.URLField("URL da mídia", max_length=500, null=True, blank=True)
    media_type = models.CharField(
        "tipo da mídia", max_length=20, choices=MEDIA_TYPES, blank=True, default=""
    )
    mail_template = models.CharField("layout do e-mail", max_length=80, default="default")
    active = models.BooleanField("ativo", default=True)
    context_keys = models.JSONField("campos disponíveis", default=list, editable=False)
    default_hash = models.CharField(max_length=64, editable=False)
    customized_at = models.DateTimeField("personalizado em", null=True, blank=True, editable=False)
    customized_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="personalizado por",
        null=True,
        blank=True,
        editable=False,
        on_delete=models.SET_NULL,
        related_name="customized_notification_templates",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("event",)
        verbose_name = "conteúdo de notificação"
        verbose_name_plural = "conteúdos de notificação"

    def __str__(self) -> str:
        return self.event

    @property
    def channel_names(self) -> tuple[str, ...]:
        return parse_channels(self.channels)

    def clean(self):
        super().clean()
        if self.pk:
            original = type(self).objects.filter(pk=self.pk).values_list("event", flat=True).first()
            if original is not None and original != self.event:
                raise ValidationError({"event": "O evento é fixo e não pode ser renomeado."})

        channel_names = self.channel_names
        unknown_channels = set(channel_names) - CHANNELS
        if not channel_names:
            raise ValidationError({"channels": "Informe ao menos um canal."})
        if unknown_channels:
            raise ValidationError(
                {"channels": f"Canais desconhecidos: {', '.join(sorted(unknown_channels))}."}
            )
        if not self.body.strip():
            raise ValidationError({"body": "O conteúdo não pode ficar vazio."})
        validate_media_url(self.media_url)

        unknown_keys = placeholders(self.title, self.subject, self.body) - set(self.context_keys)
        if unknown_keys:
            raise ValidationError(
                {
                    "body": (
                        "Campos não fornecidos pelo backend: "
                        f"{', '.join(sorted(unknown_keys))}."
                    )
                }
            )
        story_keys = placeholders(self.story_prompt)
        if self.storytelling and not (self.story_prompt or "").strip():
            raise ValidationError(
                {"story_prompt": "Informe a instrução quando a personalização por IA estiver ativa."}
            )
        unknown_story_keys = story_keys - STORY_PROMPT_KEYS
        if unknown_story_keys:
            raise ValidationError(
                {
                    "story_prompt": (
                        "Campos de história desconhecidos: "
                        f"{', '.join(sorted(unknown_story_keys))}."
                    )
                }
            )

    def save(self, *args, **kwargs):
        if self.pk:
            original = type(self).objects.filter(pk=self.pk).values_list("event", flat=True).first()
            if original is not None and original != self.event:
                raise ValidationError({"event": "O evento é fixo e não pode ser renomeado."})
        return super().save(*args, **kwargs)
