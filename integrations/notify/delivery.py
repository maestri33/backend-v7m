from __future__ import annotations

import uuid

import structlog
from django.db import transaction

logger = structlog.get_logger()

_MEDIA_EXTENSIONS = {
    "image": {"png", "jpg", "jpeg", "gif", "webp", "bmp", "svg"},
    "video": {"mp4", "mov", "avi", "mkv", "webm", "3gp"},
    "audio": {"mp3", "ogg", "wav", "m4a", "aac", "opus"},
}


def _guess_media_type(url: str) -> str:
    extension = url.rsplit("?", 1)[0].rsplit("/", 1)[-1].rsplit(".", 1)[-1].lower()
    return next(
        (kind for kind, extensions in _MEDIA_EXTENSIONS.items() if extension in extensions),
        "document",
    )


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
    request_id = idempotency_key or str(uuid.uuid4())
    payload = {
        "text": text,
        "caller": caller,
        "phone": phone,
        "email": email,
        "title": title,
        "subject": subject,
        "whatsapp": whatsapp,
        "email_channel": email_channel,
        "tts": tts,
        "media_url": media_url,
        "media_type": media_type or (_guess_media_type(media_url) if media_url else None),
        "gender": gender,
        "mail_template": mail_template,
        "external_id": request_id,
        "run_sync": run_sync,
    }
    if run_sync:
        from integrations.notify import client

        return str(client.post_send(payload, run_sync=True)["external_id"])

    from django_q.tasks import async_task

    transaction.on_commit(
        lambda: async_task("integrations.notify.tasks.push_send", payload)
    )
    logger.info("notify.remote_queued", external_id=request_id, caller=caller)
    return request_id
