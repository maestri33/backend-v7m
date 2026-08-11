"""`send_event()` — despacho orientado a evento, a frente do notify.

O caller diz só o EVENTO + destinatário + ctx; teor/canais/trigger/storytelling são resolvidos pelo
**notify-server** (Fase 2 — `remote` é o único modo desde a aposentadoria do adapter local). O
destinatário é resolvido AQUI (o servidor não conhece Profile) e o resto vai no payload.

Placeholders (`{nome}`, `{nome-completo}`, `{valor}`, `{link}`...) são resolvidos server-side;
`nome`/`nome_completo` saem do `profile.name` quando `profile` (ou `user`) é informado.
"""

from __future__ import annotations

import uuid

import structlog
from django.db import transaction

logger = structlog.get_logger()


def _resolve_profile(user, profile):
    """Devolve (profile, nome, nome_completo, phone, email, gender, birth_date)."""
    if profile is None and user:
        from users.profiles import interface as profiles

        # user pode ser external_id (str) ou User; find_by_external_id aceita str.
        uid = getattr(user, "external_id", user)
        profile = profiles.find_by_external_id(str(uid))
    if profile is None:
        return None, None, None, None, None, None, None
    name = (getattr(profile, "name", None) or "").strip() or None
    from users.roles import notifications as msgs

    return (
        profile,
        msgs.first_name(name),
        msgs.full_name(name),
        getattr(profile, "phone", None) or None,
        getattr(profile, "email", None) or None,
        getattr(profile, "gender", None) or None,
        getattr(profile, "birth_date", None),
    )


def send_event(
    event: str,
    *,
    user=None,
    profile=None,
    phone: str | None = None,
    email: str | None = None,
    ctx: dict | None = None,
    title: str | None = None,
    subject: str | None = None,
    media_url: str | None = None,
    media_type: str | None = None,
    gender: str | None = None,
    mail_template: str | None = None,
    idempotency_key: str | None = None,
    run_sync: bool = False,
    body_md_override: str | None = None,
    is_tts_override: bool | None = None,
    channels_override: tuple[str, ...] | list[str] | None = None,
) -> str | None:
    """Despacha a notificação do `event` ao destinatário (via notify-server). Devolve o `external_id`
    (ou None se, no `run_sync`, o servidor recusar — trigger inativo / evento desconhecido / sem canal).

    - `user` (external_id ou User) ou `profile`: resolve phone/email/nome do Profile.
    - `phone`/`email`: sobrescreve o do Profile (destino livre).
    - `ctx`: placeholders extras (`valor`, `link`, `detail`, ...). `nome`/`nome_completo` injetados.
    - overrides (`title`/`subject`/`media_url`/`gender`/`mail_template`) vencem o Template.
    - `body_md_override`: corpo JÁ renderizado pelo caller (pula o teor/storytelling do servidor).
    - `is_tts_override` / `channels_override`: forçam modo TTS / canais ignorando o Template.
    """
    return _send_event_remote(
        event,
        user=user,
        profile=profile,
        phone=phone,
        email=email,
        ctx=ctx,
        title=title,
        subject=subject,
        media_url=media_url,
        media_type=media_type,
        gender=gender,
        mail_template=mail_template,
        idempotency_key=idempotency_key,
        run_sync=run_sync,
        body_md_override=body_md_override,
        is_tts_override=is_tts_override,
        channels_override=channels_override,
    )


def _send_event_remote(
    event: str,
    *,
    user,
    profile,
    phone: str | None,
    email: str | None,
    ctx: dict | None,
    title: str | None,
    subject: str | None,
    media_url: str | None,
    media_type: str | None,
    gender: str | None,
    mail_template: str | None,
    idempotency_key: str | None,
    run_sync: bool,
    body_md_override: str | None,
    is_tts_override: bool | None,
    channels_override: tuple[str, ...] | list[str] | None,
) -> str | None:
    """Resolve o destinatário AQUI (o servidor não conhece Profile) e delega teor/canais/trigger ao
    notify-server (shape SendEventIn).

    No caminho async, trigger inativo / evento desconhecido devolvem o uuid do cliente (não None) —
    o descarte acontece (e é logado) no push. O storytelling continua no backend nesta fase:
    `story_or_none` gera e vai como `body_md_override` (override do caller tem precedência).
    """
    from users.roles import notifications as msgs

    _prof, nome, nome_completo, p_phone, p_email, p_gender, birth = _resolve_profile(
        user, profile
    )
    phone = phone or p_phone
    email = email or p_email
    gender = gender or p_gender

    # pré-check barato: sem destino algum não há o que despachar.
    if not phone and not email:
        logger.warning("notify.event_no_recipient", event_key=event)
        return None

    if body_md_override is None:
        body_md_override = msgs.story_or_none(
            event, name=nome or "tudo bem", age=msgs.age_from(birth)
        )

    client_uuid = str(uuid.uuid4())
    server_key = idempotency_key or client_uuid
    payload = {
        "event": event,
        "phone": phone,
        "email": email,
        "nome": nome,
        "nome_completo": nome_completo,
        "gender": gender,
        "ctx": ctx,
        "title": title,
        "subject": subject,
        "media_url": media_url,
        "media_type": media_type,
        "mail_template": mail_template,
        # no /v1/send-event a chave de idempotência tem o nome literal (recon R6).
        "idempotency_key": server_key,
        "body_md_override": body_md_override,
        "is_tts_override": is_tts_override,
        "channels_override": (
            list(channels_override) if channels_override is not None else None
        ),
        "run_sync": run_sync,
    }

    if run_sync:
        from notify.sdk import client as sdk

        resp = sdk.post_send_event(payload, run_sync=True)
        if resp is None:
            return None  # 404: evento inexistente/trigger inativo/sem canal
        return str(resp["external_id"])

    from django_q.tasks import async_task

    # POST só depois do commit (§12): rollback do caller não pode virar mensagem enviada.
    transaction.on_commit(
        lambda: async_task("notify.sdk.push.push_send_event", payload)
    )
    logger.info(
        "notify.sdk.remote_queued",
        external_id=client_uuid,
        event_key=event,
        has_key=bool(idempotency_key),
    )
    return client_uuid
