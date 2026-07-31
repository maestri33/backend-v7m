"""Superfície pública in-process do notify (CONVENTION §3): o que outros apps do monólito chamam.

Uso (de outro app do Django):

    from notify.interface.send import send
    send(text="Olá 👋", caller="asaas.charge", phone="5543996648750", whatsapp=True)
    # com imagem (WhatsApp busca pela LAN, e-mail embute pela URL pública):
    send(text="Seu QR", caller="asaas.charge", phone="55...", email="a@b.com", email_channel=True,
         media_url="https://dev.m33.live/media/qrcodes/pay_x.png", media_type="image")

Persiste a intenção ANTES de enviar (auditoria/idempotência §8), enfileira o despacho no
Django-Q e devolve o `external_id` na hora — NUNCA bloqueia o fluxo do caller (§12).
"""

from __future__ import annotations

import uuid

import structlog
from django.conf import settings
from django.db import IntegrityError, transaction

from notify.models import STATUS_PENDING, STATUS_SKIPPED, Notification
from users.exceptions import NotFound, ValidationError

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


def local_idempotent_hit(idempotency_key: str | None, *, caller: str) -> str | None:
    """Se `idempotency_key` já foi usada, devolve o external_id existente — senão None.

    Chamado ANTES do branch de modo (local ou remote): a tabela local é a MESMA nos dois modos,
    então isso fecha a duplicidade do flip local→remote (uma chave reenviada logo após o flip
    acha o hit aqui em vez de criar Notification nova no servidor). Não cobre o sentido inverso
    (remote→local, rollback): risco residual aceito — rollback é caminho de emergência, não
    flip rotineiro; ver wiki/notify/servico-multi-tenant.md.
    """
    if not idempotency_key:
        return None
    existing = Notification.objects.filter(idempotency_key=idempotency_key).first()
    if existing is None:
        return None
    logger.info(
        "notify.idempotent_hit", external_id=str(existing.external_id), caller=caller
    )
    return str(existing.external_id)


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
    mídia: WhatsApp busca pela LAN, e-mail embute pela URL pública; `media_type` é auto-detectado
    pela extensão se não vier. `gender` (M/F) escolhe a voz do TTS (resolvido no integrations.ai).
    `run_sync=True` roda o despacho inline (testes/commands); o default é assíncrono (Django-Q).
    """
    hit = local_idempotent_hit(idempotency_key, caller=caller)
    if hit is not None:
        return hit

    if settings.NOTIFY_MODE == "remote":
        return _send_remote(
            text=text,
            caller=caller,
            phone=phone,
            email=email,
            title=title,
            subject=subject,
            whatsapp=whatsapp,
            email_channel=email_channel,
            tts=tts,
            media_url=media_url,
            media_type=media_type,
            gender=gender,
            mail_template=mail_template,
            idempotency_key=idempotency_key,
            run_sync=run_sync,
        )

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


def _send_remote(
    *,
    text: str,
    caller: str,
    phone: str | None,
    email: str | None,
    title: str | None,
    subject: str | None,
    whatsapp: bool,
    email_channel: bool,
    tts: bool,
    media_url: str | None,
    media_type: str | None,
    gender: str | None,
    mail_template: str,
    idempotency_key: str | None,
    run_sync: bool,
) -> str:
    """Modo remote (Fase 2): delega o envio ao notify-server via SDK, preservando o contrato.

    Chamado só quando `local_idempotent_hit` (acima) não achou nada local. Async: enfileira o
    POST no Django-Q só DEPOIS do commit (§12) e devolve NA HORA um uuid gerado aqui; o servidor
    deduplica pela chave (`external_id` do payload = idempotency_key do caller, ou o próprio
    uuid), então o retry do Q re-posta a MESMA chave sem duplicar. Repetir uma chave que já foi
    usada NO SERVIDOR (mas não existe localmente) ainda devolve um uuid novo "dangling" — aceito,
    callers com chave ignoram o retorno (recon R4). Sync: POST inline; devolve o external_id REAL
    da resposta.
    """
    client_uuid = str(uuid.uuid4())
    server_key = idempotency_key or client_uuid
    if media_url and not media_type:
        media_type = _guess_media_type(media_url)  # paridade com o caminho local
    # Contrato genérico do notify: conteúdo JÁ pronto (`content`) + roteamento por `instance`.
    payload = {
        "content": text,
        "caller": caller,
        "phone": phone,
        "email": email,
        "title": title,
        "subject": subject,
        "whatsapp": whatsapp,
        "email_channel": email_channel,
        "tts": tts,
        "media_url": media_url,
        "media_type": media_type,
        "sexo": gender,
        "instance": settings.NOTIFY_INSTANCE,
        # na API /v1/send o campo chama-se external_id, mas é a idempotency_key do servidor.
        "external_id": server_key,
        "run_sync": run_sync,
    }

    if run_sync:
        from notify.sdk import client as sdk

        resp = sdk.post_send(payload, run_sync=True)
        return str(resp["external_id"])

    from django_q.tasks import async_task

    # POST só depois do commit (§12): rollback do caller não pode virar mensagem enviada.
    transaction.on_commit(lambda: async_task("notify.sdk.push.push_send", payload))
    logger.info(
        "notify.sdk.remote_queued",
        external_id=client_uuid,
        caller=caller,
        has_key=bool(idempotency_key),
    )
    return client_uuid


def get_by_external_id(external_id) -> Notification | None:
    """Busca a Notification pelo external_id (o handle de borda devolvido por `send`). None se não achar.

    Permite que outro app guarde a relação por FK (em vez do external_id solto), respeitando §3 —
    não fura o model do notify por fora.
    """
    if not external_id:
        return None
    return Notification.objects.filter(external_id=external_id).first()


def send_adhoc(
    *,
    message: str,
    to_user: str | None = None,
    phone: str | None = None,
    email: str | None = None,
    subject: str | None = None,
    channels: list[str] | None = None,
    caller: str = "notify.adhoc",
) -> str:
    """Notificação AVULSA do staff: WhatsApp e/ou e-mail a um USUÁRIO (external_id) OU a um destino
    LIVRE (phone/email sem cadastro). Devolve o `external_id` da Notification.

    - `to_user`: external_id de um User — resolve phone/email pelo Profile (não precisa digitar).
    - `phone`/`email`: destino livre (pode coexistir com `to_user` p/ sobrescrever um canal).
    - `channels`: subconjunto de {"whatsapp","email"}; default = todos os que têm destino.

    Valida na borda: mensagem não-vazia + pelo menos um destino. Reusa o dispatcher (`send`):
    enfileira no Django-Q e nunca bloqueia. NÃO loga PII (telefone/e-mail).
    """
    message = (message or "").strip()
    if not message:
        raise ValidationError("Mensagem não pode ser vazia.", code="MISSING_FIELD")

    phone = (phone or "").strip() or None
    email = (email or "").strip().lower() or None

    # usuário informado → herda phone/email do Profile (sem sobrescrever destino livre explícito).
    if to_user:
        from users.profiles import interface as profiles

        profile = profiles.find_by_external_id(to_user)
        if profile is None:
            raise NotFound("Usuário não encontrado.", code="USER_NOT_FOUND")
        phone = phone or (profile.phone or None)
        email = email or (profile.email or None)

    if not phone and not email:
        raise ValidationError(
            "Informe ao menos um destino (to_user, phone ou email).",
            code="MISSING_FIELD",
        )

    # canais: default = todos os que têm destino; senão respeita o pedido (intersecção com destino).
    requested = {c.strip().lower() for c in (channels or [])} or {"whatsapp", "email"}
    want_whatsapp = "whatsapp" in requested and bool(phone)
    want_email = "email" in requested and bool(email)
    if not want_whatsapp and not want_email:
        raise ValidationError(
            "Nenhum canal com destino válido (whatsapp exige phone; email exige email).",
            code="MISSING_FIELD",
        )

    logger.info(
        "notify.adhoc",
        caller=caller,
        whatsapp=want_whatsapp,
        email=want_email,
        has_user=bool(to_user),
    )
    return send(
        text=message,
        caller=caller,
        phone=phone if want_whatsapp else None,
        email=email if want_email else None,
        subject=subject,
        title=subject,
        whatsapp=want_whatsapp,
        email_channel=want_email,
    )
