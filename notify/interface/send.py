"""Superfície pública in-process do notify (CONVENTION §3): o que outros apps do monólito chamam.

Uso (de outro app do Django):

    from notify.interface.send import send
    send(text="Olá 👋", caller="asaas.charge", phone="5543996648750", whatsapp=True)
    # com imagem (Evolution GO busca pela LAN, e-mail embute pela URL pública):
    send(text="Seu QR", caller="asaas.charge", phone="55...", email="a@b.com", email_channel=True,
         media_url="https://dev.m33.live/media/qrcodes/pay_x.png", media_type="image")

Persiste a intenção ANTES de enviar (auditoria/idempotência §8), enfileira o despacho no
Django-Q e devolve o `external_id` na hora — NUNCA bloqueia o fluxo do caller (§12).
"""

from __future__ import annotations

import structlog
from django.db import IntegrityError, transaction

from notify.models import STATUS_PENDING, STATUS_SKIPPED, Notification

logger = structlog.get_logger()

# extensão → media_type (auto-detect quando o caller não informa). default = document.
_MEDIA_EXT = {
    "image": {"png", "jpg", "jpeg", "gif", "webp", "bmp", "svg"},
    "video": {"mp4", "mov", "avi", "mkv", "webm", "3gp"},
    "audio": {"mp3", "ogg", "wav", "m4a", "aac", "opus"},
}


def _guess_media_type(url: str) -> str:
    """Adivinha o media_type pela extensão da URL (image/video/audio); senão document."""
    tail = url.rsplit("?", 1)[0].rsplit("/", 1)[-1]
    ext = tail.rsplit(".", 1)[-1].lower() if "." in tail else ""
    for media_type, exts in _MEDIA_EXT.items():
        if ext in exts:
            return media_type
    return "document"


def send(
    *,
    text: str,
    caller: str,
    phone: str | None = None,
    email: str | None = None,
    title: str | None = None,
    subject: str | None = None,
    whatsapp: bool = True,
    email_channel: bool = False,
    tts: bool = False,
    media_url: str | None = None,
    media_type: str | None = None,
    gender: str | None = None,
    mail_template: str = "default",
    idempotency_key: str | None = None,
    run_sync: bool = False,
) -> str:
    """Cria a Notification e dispara o envio. Devolve o `external_id` (handle estável).

    Canal pedido sem destinatário nasce 'skipped' (nada a enviar). `idempotency_key` repetido
    devolve a notificação existente sem reenfileirar. `media_url` (URL pública) ativa o envio de
    mídia: Evolution GO busca pela LAN, e-mail embute pela URL pública; `media_type` é auto-detectado
    pela extensão se não vier. `gender` (M/F) escolhe a voz do TTS (resolvido no integrations.ai).
    `run_sync=True` roda o despacho inline (testes/commands); o default é assíncrono (Django-Q).
    """
    if idempotency_key:
        existing = Notification.objects.filter(idempotency_key=idempotency_key).first()
        if existing is not None:
            logger.info(
                "notify.idempotent_hit",
                external_id=str(existing.external_id),
                caller=caller,
            )
            return str(existing.external_id)

    if media_url and not media_type:
        media_type = _guess_media_type(media_url)

    # canal pedido mas sem destinatário => já nasce 'skipped'.
    wa_status = STATUS_PENDING if (whatsapp and phone) else STATUS_SKIPPED
    mail_status = STATUS_PENDING if (email_channel and email) else STATUS_SKIPPED
    tts_status = STATUS_PENDING if (tts and phone) else STATUS_SKIPPED

    try:
        with transaction.atomic():
            notif = Notification.objects.create(
                idempotency_key=idempotency_key,
                caller=caller,
                recipient_phone=phone,
                recipient_email=email,
                title=title,
                text=text,
                subject=subject,
                mail_template=mail_template,
                media_url=media_url,
                media_type=media_type,
                gender=gender,
                want_whatsapp=whatsapp,
                want_email=email_channel,
                want_tts=tts,
                whatsapp_status=wa_status,
                email_status=mail_status,
                tts_status=tts_status,
            )
    except IntegrityError:
        # corrida na idempotency_key (unique): outra chamada criou primeiro — devolve a dela.
        existing = Notification.objects.get(idempotency_key=idempotency_key)
        logger.info(
            "notify.idempotent_race",
            external_id=str(existing.external_id),
            caller=caller,
        )
        return str(existing.external_id)

    logger.info(
        "notify.queued",
        external_id=str(notif.external_id),
        caller=caller,
        whatsapp=wa_status,
        email=mail_status,
        tts=tts_status,
        media=media_type or "",
        run_sync=run_sync,
    )

    if run_sync:
        from notify.dispatch import dispatch

        dispatch(notif.id)
    else:
        from django_q.tasks import async_task

        # enfileira só depois do commit (o worker não pode pegar a task antes da linha existir).
        transaction.on_commit(lambda: async_task("notify.dispatch.dispatch", notif.id))

    return str(notif.external_id)


def get_by_external_id(external_id) -> Notification | None:
    """Busca a Notification pelo external_id (o handle de borda devolvido por `send`). None se não achar.

    Permite que outro app guarde a relação por FK (em vez do external_id solto), respeitando §3 —
    não fura o model do notify por fora.
    """
    if not external_id:
        return None
    return Notification.objects.filter(external_id=external_id).first()
