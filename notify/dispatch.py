"""Despacho do notify (rodado pelo Django-Q, síncrono) — envia uma Notification por canal.

Cada canal é isolado em try/except: a falha de um não derruba os outros nem o caller (§12). Os
clientes externos do `integrations/` são async → rodam via `async_to_sync` (padrão do codebase);
`ai.service.tts` já é síncrona. Retry simples: uma tentativa por passada — o Django-Q re-executa a
task em caso de erro não tratado (aqui tratamos por canal, então a task termina "ok" com o status).

Entrega do WhatsApp (Victor 2026-07-02 — TTS como MODO de entrega, não canal paralelo):
 - se `media_url`  → envia MÍDIA (legenda = body); TTS fica SKIPPED (mídia tem precedência).
 - senão se `want_tts` → tenta voice-note (áudio); se a IA falhar, CAI PRA TEXTO (fallback).
 - senão → envia TEXTO.
E-mail SEMPRE texto (md→HTML); o áudio nunca vai por e-mail. Mídia (§0.2): WhatsApp busca pela URL
LAN (IP interno, sem egress — `_to_lan`); e-mail embute pela URL pública.
"""

from __future__ import annotations

import re

import structlog
from asgiref.sync import async_to_sync
from django.conf import settings
from django.db import transaction

from integrations.communication.mail import client as mail_client
from integrations.communication.mail import templates as mail_templates
from integrations.communication.whatsapp.client import get_client as get_whatsapp_client
from integrations.ai import service as ai_service
from notify import sanitize
from notify.models import (
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_SENDING,
    STATUS_SENT,
    STATUS_SKIPPED,
    Notification,
)

logger = structlog.get_logger()


def dispatch(notification_id: int) -> None:
    """Envia a Notification pelos canais ainda pendentes e grava o status de cada um.

    G16 — três fases pra não duplicar envio quando o worker morre no meio (OOM/timeout do Django-Q,
    que re-executa a task após `retry`):

      1. CLAIM: sob `select_for_update`, marca os canais PENDENTES como SENDING e COMMITA. A trava
         serializa execuções concorrentes (o 2º worker vê SENDING, não PENDING), e o commit persiste
         o "estou enviando" ANTES de qualquer chamada de rede.
      2. ENVIO: FORA da transação. A rede (WhatsApp/e-mail/IA-TTS) não é desfeita por rollback, então
         não pode rodar dentro de uma transação que pode reverter — era essa a causa da duplicação:
         o áudio ia pro aluno, a transação revertia o status pra PENDING, e o retry reenviava.
      3. RESULTADO: grava SENT/FAILED de cada canal.

    Recuperação (regra do Victor): se uma execução anterior morreu no meio (canal em SENDING), o
    retry RECUPERA por TEXTO — não regenera o TTS (a geração de áudio é a única coisa que custa IA e
    não pode duplicar). Assim a mensagem sempre chega (texto é comum e sem problema); o áudio, se
    saiu antes do crash, o aluno recebe além do texto. Nunca fica sem mensagem, nunca cobra a IA 2×.
    """
    # ── FASE 1: CLAIM ────────────────────────────────────────────────────────
    with transaction.atomic():
        notif = (
            Notification.objects.select_for_update().filter(id=notification_id).first()
        )
        if notif is None:
            logger.warning("notify.dispatch_missing", id=notification_id)
            return

        notif.attempts += 1

        # A4 — TEST_MODE: dry-run — marca todos os canais pendentes como SENT e NÃO envia nada pela
        # rede (sem WhatsApp/e-mail/TTS). O caller (ex.: OTP service) completa normalmente porque só
        # lê o status; nada chega a um destinatário real.
        if getattr(settings, "TEST_MODE", False):
            if notif.whatsapp_status == STATUS_PENDING:
                notif.whatsapp_status = STATUS_SENT
            if notif.email_status == STATUS_PENDING:
                notif.email_status = STATUS_SENT
            if notif.tts_status == STATUS_PENDING:
                notif.tts_status = STATUS_SENT
            notif.save()
            logger.info(
                "notify.dispatched_dry_run",
                external_id=str(notif.external_id),
                caller=notif.caller,
            )
            return

        # PENDING = nunca tentado. SENDING = execução anterior morreu no meio → RECUPERA por TEXTO
        # (regra do Victor): garante a entrega SEM regenerar o TTS (a geração de áudio é a única
        # coisa cara/não-duplicável, pois custa IA). Texto e e-mail são baratos e reenviáveis "como
        # é comum, sem problema" — se o áudio chegou a sair antes do crash, o aluno recebe áudio +
        # texto; se não saiu, recebe só o texto. Nunca fica sem mensagem.
        wa_pending = notif.whatsapp_status == STATUS_PENDING
        wa_recover = notif.whatsapp_status == STATUS_SENDING
        email_pending = notif.email_status == STATUS_PENDING
        email_recover = notif.email_status == STATUS_SENDING
        tts_pending = notif.want_tts and notif.tts_status == STATUS_PENDING

        do_whatsapp = wa_pending or wa_recover
        do_email = email_pending or email_recover
        if wa_recover or email_recover:
            logger.warning(
                "notify.recovering_as_text",
                external_id=str(notif.external_id),
                whatsapp=wa_recover,
                email=email_recover,
            )

        if not (do_whatsapp or do_email):
            notif.save(update_fields=["attempts"])
            return

        # marca o CLAIM (SENDING) nos canais que vão ser enviados e COMMITA (fim do `with`). O TTS só
        # é reclamado quando é um envio NOVO (tts_pending); na recuperação, ele NÃO é regenerado.
        if do_whatsapp:
            notif.whatsapp_status = STATUS_SENDING
        if do_email:
            notif.email_status = STATUS_SENDING
        if tts_pending:
            notif.tts_status = STATUS_SENDING
        notif.save()

    # ── FASE 2: ENVIO (fora da transação) ────────────────────────────────────
    # Usa as flags do_*/*_recover (não os status, que agora são SENDING). Os _send_* mutam notif.
    if do_whatsapp:
        if wa_recover:
            # RECUPERAÇÃO: execução anterior interrompida → manda TEXTO, sem regenerar o TTS.
            if notif.tts_status == STATUS_SENDING:
                notif.tts_status = STATUS_FAILED
                notif.tts_error = "recuperado como texto (envio anterior interrompido)"
            _send_whatsapp_text(notif)
        elif notif.media_url:
            if tts_pending:
                notif.tts_status = (
                    STATUS_SKIPPED  # mídia tem precedência sobre voice-note
                )
            _send_whatsapp_media(notif)
        elif notif.want_tts:
            _send_tts(
                notif
            )  # áudio; falha SÍNCRONA → texto (fallback interno já existente)
        else:
            _send_whatsapp_text(notif)
    elif tts_pending:
        # whatsapp sem destino (SKIPPED) mas TTS pendente: áudio não tem pra onde ir → skipa.
        notif.tts_status = STATUS_SKIPPED

    if do_email:
        _send_email(notif)

    # ── FASE 3: RESULTADO ────────────────────────────────────────────────────
    with transaction.atomic():
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
    """URL pública → LAN p/ a Evolution buscar a mídia pelo IP interno (sem egress), como o legado.

    Troca o prefixo EXTERNAL_URL (ex.: https://dev.m33.live) pelo MEDIA_LAN_BASE (ex.:
    http://10.1.20.30). URL que não começa com a pública é devolvida como está.
    """
    lan = settings.MEDIA_LAN_BASE
    ext = settings.EXTERNAL_URL
    if lan and ext and url.startswith(ext):
        return lan + url[len(ext) :]
    return url


def _whatsapp_body(notif: Notification) -> str:
    """Texto do WhatsApp: título em negrito + corpo, com o markdown do body convertido p/ a
    marcação nativa do WhatsApp (o body é escrito em md — no e-mail vira HTML via md_to_html)."""
    body = sanitize.for_whatsapp(notif.text)
    if notif.title:
        return f"*{notif.title}*\n\n{body}"
    return body


def _send_whatsapp_text(notif: Notification) -> None:
    """Envia só texto (sem mídia) pelo WhatsApp."""

    async def _run():
        async with get_whatsapp_client() as wa:
            number = await wa.resolve_br_number(notif.recipient_phone)
            return await wa.send_text(number, _whatsapp_body(notif))

    try:
        async_to_sync(_run)()
        notif.whatsapp_status = STATUS_SENT
    except Exception as exc:  # noqa: BLE001 — um canal não derruba os outros (§12)
        notif.whatsapp_status = STATUS_FAILED
        notif.whatsapp_error = f"{type(exc).__name__}: {exc}"
        logger.warning(
            "notify.whatsapp_failed", external_id=str(notif.external_id), error=str(exc)
        )


def _send_whatsapp_media(notif: Notification) -> None:
    """Envia mídia (URL LAN) com legenda = body. E-mail embute a mesma mídia pela URL pública."""

    async def _run():
        async with get_whatsapp_client() as wa:
            number = await wa.resolve_br_number(notif.recipient_phone)
            wa_url = _to_lan(notif.media_url)
            return await wa.send_media(
                number,
                wa_url,
                notif.media_type or "document",
                caption=_whatsapp_body(notif),
            )

    try:
        async_to_sync(_run)()
        notif.whatsapp_status = STATUS_SENT
    except Exception as exc:  # noqa: BLE001 — isola a falha do canal (§12)
        notif.whatsapp_status = STATUS_FAILED
        notif.whatsapp_error = f"{type(exc).__name__}: {exc}"
        logger.warning(
            "notify.whatsapp_failed", external_id=str(notif.external_id), error=str(exc)
        )


def _subject_from_body(text: str) -> str:
    """Assunto derivado do corpo quando o template não define subject/title.

    Quase todos os 48 templates do seed nascem com subject/title vazios, e sem isto o e-mail saía
    com o literal "(sem assunto)". Pega a 1ª frase, tira markdown e a saudação inicial ("{nome}, "
    — já renderizada como o nome real no `text`), capitaliza e corta em ~78 chars.
    """
    if not text:
        return ""
    line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    line = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", line)  # [txt](url) -> txt
    line = re.sub(r"[*_`~]", "", line)  # tira markdown de ênfase
    line = re.sub(
        r"^[\wÀ-ÿ'.\- ]{1,30}?,\s+", "", line, count=1
    )  # remove "Nome, " inicial
    m = re.match(r"(.+?[.!?])(?:\s|$)", line)  # 1ª frase, se longa o bastante
    sent = m.group(1) if m and len(m.group(1)) >= 12 else line
    sent = sent.strip().rstrip(".!?")
    if not sent:
        return ""
    sent = sent[0].upper() + sent[1:]
    if len(sent) > 78:
        sent = sent[:77].rsplit(" ", 1)[0] + "…"
    return sent


def _send_email(notif: Notification) -> None:
    try:
        if notif.mail_template == "v7m":
            from_name = "V7M"
        elif notif.mail_template in {
            "supletivo",
            "checkout",
            "parabens",
            "receipt",
            "welcome",
        }:
            from_name = "Supletivo Brasil"
        else:
            from_name = settings.MAIL_FROM_NAME
        subject = (
            notif.subject
            or notif.title
            or _subject_from_body(notif.text)
            or from_name
        )
        if notif.media_url:
            # e-mail embute a mídia pela URL PÚBLICA (destinatário busca pela internet).
            content_html = mail_templates.md_to_html(
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
        client = mail_client.get_client(from_name=from_name)
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
    """Tenta voice-note (áudio). Se a IA falhar, CAI PRA TEXTO (WhatsApp) — o marco nunca fica sem
    mensagem. E-mail já foi/será enviado como texto por `_send_email` (áudio nunca vai pro e-mail)."""
    try:
        # ai.tts gera o mp3 e devolve o caminho RELATIVO a MEDIA_ROOT (ex.: "ai/audio/<uuid>.mp3").
        # gender (M/F) escolhe a voz; sanitiza o texto pra leitura (tira markdown/URL/emoji).
        speakable = sanitize.for_tts(notif.text)
        rel_path = ai_service.tts(
            speakable, caller=f"notify:{notif.caller}", gender=notif.gender or None
        )
        notif.tts_audio_path = rel_path
        base = settings.MEDIA_LAN_BASE or settings.EXTERNAL_URL
        # ponytail: urljoin evita "//" quando base termina em "/" (EXTERNAL_URL comum em prod)
        from urllib.parse import urljoin

        audio_url = urljoin(base + "/", settings.MEDIA_URL + rel_path)

        async def _run():
            async with get_whatsapp_client() as wa:
                number = await wa.resolve_br_number(notif.recipient_phone)
                return await wa.send_whatsapp_audio(number, audio_url)

        async_to_sync(_run)()
        notif.tts_status = STATUS_SENT
        notif.whatsapp_status = STATUS_SENT  # entregue (como áudio)
    except Exception as exc:  # noqa: BLE001 — fallback texto: áudio falhou, mas o marco não fica mudo
        notif.tts_status = STATUS_FAILED
        notif.tts_error = f"{type(exc).__name__}: {exc}"
        logger.warning(
            "notify.tts_failed_fallback_text",
            external_id=str(notif.external_id),
            error=str(exc),
        )
        # fallback: entrega o corpo como TEXTO no WhatsApp (idempotente: só se ainda não foi enviado).
        if notif.whatsapp_status != STATUS_SENT:
            _send_whatsapp_text(notif)
