"""Despacho do notify (rodado pelo Django-Q, síncrono) — envia uma Notification por canal.

Cada canal é isolado em try/except: a falha de um não derruba os outros nem o caller (§12). Os
clientes externos do `integrations/` são async → rodam via `async_to_sync` (padrão do codebase);
`ai.service.tts` já é síncrona. Retry simples: uma tentativa por passada — o Django-Q re-executa
a task em caso de erro não tratado (aqui tratamos por canal, então a task termina "ok" com o
status gravado).

Mídia (§0.2 do plano): Evolution GO busca a mídia/áudio pela URL **LAN** (IP interno, sem egress —
`_to_lan`, padrão do legado); o e-mail embute a mídia pela URL **pública** (o cliente do
destinatário busca pela internet).
"""

from __future__ import annotations

import structlog
from asgiref.sync import async_to_sync
from django.conf import settings

from integrations.communication.mail import client as mail_client
from integrations.communication.mail import templates as mail_templates
from integrations.communication.whatsapp.client import get_client as get_whatsapp_client
from integrations.ai import service as ai_service
from notify.models import STATUS_FAILED, STATUS_PENDING, STATUS_SENT, Notification

logger = structlog.get_logger()


def dispatch(notification_id: int) -> None:
    """Envia a Notification pelos canais ainda pendentes e grava o status de cada um."""
    notif = Notification.objects.filter(id=notification_id).first()
    if notif is None:
        logger.warning("notify.dispatch_missing", id=notification_id)
        return

    notif.attempts += 1

    if notif.whatsapp_status == STATUS_PENDING:
        _send_whatsapp(notif)
    if notif.email_status == STATUS_PENDING:
        _send_email(notif)
    if notif.tts_status == STATUS_PENDING:
        _send_tts(notif)

    notif.save()
    logger.info(
        "notify.dispatched",
        external_id=str(notif.external_id),
        caller=notif.caller,
        whatsapp=notif.whatsapp_status,
        email=notif.email_status,
        tts=notif.tts_status,
        attempts=notif.attempts,
    )


def _to_lan(url: str) -> str:
    """URL pública → LAN p/ o Evolution GO buscar a mídia pelo IP interno.

    Troca o prefixo EXTERNAL_URL (ex.: https://dev.m33.live) pelo MEDIA_LAN_BASE (ex.:
    http://10.1.20.30). URL que não começa com a pública é devolvida como está.
    """
    lan = settings.MEDIA_LAN_BASE
    ext = settings.EXTERNAL_URL
    if lan and ext and url.startswith(ext):
        return lan + url[len(ext) :]
    return url


def _whatsapp_body(notif: Notification) -> str:
    """Texto do WhatsApp: título em negrito + corpo (sem título, só o corpo)."""
    if notif.title:
        return f"*{notif.title}*\n\n{notif.text}"
    return notif.text


def _send_whatsapp(notif: Notification) -> None:
    async def _run():
        async with get_whatsapp_client() as wa:
            number = await wa.resolve_br_number(notif.recipient_phone)
            if notif.media_url:
                # WhatsApp busca a mídia pela URL LAN (IP interno); legenda = corpo da mensagem.
                wa_url = _to_lan(notif.media_url)
                return await wa.send_media(
                    number,
                    wa_url,
                    notif.media_type or "document",
                    caption=_whatsapp_body(notif),
                )
            return await wa.send_text(number, _whatsapp_body(notif))

    try:
        async_to_sync(_run)()
        notif.whatsapp_status = STATUS_SENT
    except Exception as exc:  # noqa: BLE001 — um canal não pode derrubar os outros (§12)
        notif.whatsapp_status = STATUS_FAILED
        notif.whatsapp_error = f"{type(exc).__name__}: {exc}"
        logger.warning(
            "notify.whatsapp_failed", external_id=str(notif.external_id), error=str(exc)
        )


def _send_email(notif: Notification) -> None:
    try:
        subject = notif.subject or notif.title or "(sem assunto)"
        if notif.media_url:
            # e-mail embute a mídia pela URL PÚBLICA (destinatário busca pela internet).
            content_html = mail_templates.text_to_html(
                notif.text
            ) + mail_templates.media_html(
                notif.media_url,
                notif.media_type or "document",
                caption=notif.title or "",
            )
            html = mail_templates.render(
                notif.mail_template,
                title=notif.title or "",
                content=content_html,
                content_is_html=True,
            )
        else:
            html = mail_templates.render(
                notif.mail_template, title=notif.title or "", content=notif.text
            )
        client = mail_client.get_client()
        async_to_sync(client.send_email)(
            notif.recipient_email, subject, html_body=html, plain_body=notif.text
        )
        notif.email_status = STATUS_SENT
    except Exception as exc:  # noqa: BLE001 — isola a falha do canal (§12)
        notif.email_status = STATUS_FAILED
        notif.email_error = f"{type(exc).__name__}: {exc}"
        logger.warning(
            "notify.email_failed", external_id=str(notif.external_id), error=str(exc)
        )


def _send_tts(notif: Notification) -> None:
    try:
        # ai.tts gera o mp3 e devolve o caminho RELATIVO a MEDIA_ROOT (ex.: "ai/audio/<uuid>.mp3").
        # gender (M/F) escolhe a voz — a resolução gênero→voz mora no integrations.ai (§7 do plano).
        rel_path = ai_service.tts(
            notif.text, caller=f"notify:{notif.caller}", gender=notif.gender or None
        )
        notif.tts_audio_path = rel_path
        # O Evolution GO busca a URL: usa a base LAN (IP interno, sem egress/TLS —
        # padrão do legado `_to_lan`); sem ela, cai na pública EXTERNAL_URL.
        base = settings.MEDIA_LAN_BASE or settings.EXTERNAL_URL
        audio_url = f"{base}{settings.MEDIA_URL}{rel_path}"

        async def _run():
            async with get_whatsapp_client() as wa:
                number = await wa.resolve_br_number(notif.recipient_phone)
                return await wa.send_whatsapp_audio(number, audio_url)

        async_to_sync(_run)()
        notif.tts_status = STATUS_SENT
    except Exception as exc:  # noqa: BLE001 — isola a falha do canal (§12)
        notif.tts_status = STATUS_FAILED
        notif.tts_error = f"{type(exc).__name__}: {exc}"
        logger.warning(
            "notify.tts_failed", external_id=str(notif.external_id), error=str(exc)
        )
