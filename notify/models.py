"""Models do app notify — auditoria de notificações despachadas por canal.

Convenções (CONVENTION §4/§6/§12):
 - PK = BigAutoField interno (Django), nunca exposto.
 - `external_id` (UUID) = handle de borda/estável; é o retorno do `interface.send()`.
 - notify NÃO guarda contato (contato é do `profiles`, §4-3): o caller passa phone/email —
   dispatcher puro.
 - Envio é assíncrono (Django-Q) e nunca quebra o fluxo do caller (§12). Status por canal.
 - Mídia vai por URL: Evolution GO busca pela LAN, e-mail embute pela URL pública (§0.2).
"""

from django.db import models

from core.models import ExternalIdModel

# Status de envio por canal.
STATUS_PENDING = "pending"
STATUS_SENT = "sent"
STATUS_FAILED = "failed"
STATUS_SKIPPED = (
    "skipped"  # canal não pedido, ou pedido sem destinatário — nada a enviar
)

_STATUS_CHOICES = [
    (STATUS_PENDING, "pendente"),
    (STATUS_SENT, "enviado"),
    (STATUS_FAILED, "falhou"),
    (STATUS_SKIPPED, "ignorado"),
]

# Tipos de mídia (espelha integrations.communication: whatsapp.send_media / mail.media_html).
MEDIA_TYPES = ("image", "video", "audio", "document")


class Notification(ExternalIdModel):
    """Uma notificação despachada — pode atingir vários canais (whatsapp / email / tts)."""

    # dedup opcional: o caller passa uma chave estável; a mesma chave devolve a notificação já criada.
    idempotency_key = models.CharField(
        max_length=255, unique=True, null=True, blank=True
    )
    # quem emitiu a notificação (ex.: "asaas.charge") — auditoria/telemetria.
    caller = models.CharField(max_length=100)

    recipient_phone = models.CharField(max_length=32, null=True, blank=True)
    recipient_email = models.EmailField(null=True, blank=True)

    title = models.CharField(max_length=200, null=True, blank=True)
    text = models.TextField()
    subject = models.CharField(max_length=255, null=True, blank=True)
    # slug do template HTML do app mail (default/welcome/parabens/receipt/checkout).
    mail_template = models.CharField(max_length=50, default="default")

    # mídia (imagem/vídeo/áudio/documento) por URL pública: WhatsApp busca pela LAN (_to_lan),
    # e-mail embute pela URL pública. media_type = image/video/audio/document.
    media_url = models.CharField(max_length=500, null=True, blank=True)
    media_type = models.CharField(max_length=20, null=True, blank=True)

    # gênero do destinatário (M/F) → voz do TTS (resolvido no integrations.ai). Vazio = voz default.
    gender = models.CharField(max_length=1, null=True, blank=True)

    want_whatsapp = models.BooleanField(default=True)
    want_email = models.BooleanField(default=False)
    want_tts = models.BooleanField(default=False)

    whatsapp_status = models.CharField(
        max_length=10, choices=_STATUS_CHOICES, default=STATUS_PENDING
    )
    email_status = models.CharField(
        max_length=10, choices=_STATUS_CHOICES, default=STATUS_PENDING
    )
    tts_status = models.CharField(
        max_length=10, choices=_STATUS_CHOICES, default=STATUS_PENDING
    )

    whatsapp_error = models.TextField(null=True, blank=True)
    email_error = models.TextField(null=True, blank=True)
    tts_error = models.TextField(null=True, blank=True)

    # caminho (relativo a MEDIA_ROOT) do mp3 gerado pelo TTS, quando houver.
    tts_audio_path = models.CharField(max_length=500, null=True, blank=True)

    attempts = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Notification({self.external_id}, caller={self.caller})"
