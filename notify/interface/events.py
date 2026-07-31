"""`send_event()` — despacho orientado a evento (Template no DB), a frente de negócio do notify.

Diferente do `send()` (flags explícitas), aqui o caller diz só o EVENTO + destinatário + ctx, e o
Template resolve: teor (md), canais default, mídia anexa, is_tts (modo áudio do WhatsApp),
storytelling (LLM). `Trigger.active=False` desliga o evento sem código.

O teor é resolvido SEMPRE aqui (o supletivo é dono do domínio): DB (`notify.Template`) é fonte de
verdade e o catálogo in-memory (`users.roles.notifications`) é o fallback. O resultado — conteúdo
JÁ pronto — é entregue ao `send()`, que decide o transporte: dispatcher local ou POST /v1/send ao
notify-server genérico (NOTIFY_MODE=remote). O notify-server não conhece evento nem Template.

Placeholders: `{nome}` (1º nome), `{nome-completo}` (nome todo), e os legados (`{valor}`, `{link}`...).
`nome`/`nome_completo` são resolvididos do `profile.name` quando `profile` (ou `user`) é informado.
"""

from __future__ import annotations

import structlog

from notify.interface import send as _send_iface
from notify.interface import templates as _db
from notify.models import Trigger

logger = structlog.get_logger()

# canais default quando o Template não existe no DB (catálogo in-memory legado).
_DEFAULT_CHANNELS = ("whatsapp", "email")


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
    """Despacha a notificação do `event` ao destinatário. Devolve o `external_id` (ou None se o
    Trigger estiver inativo / não houver destinatário em canal algum).

    - `user` (external_id ou User) ou `profile`: resolve phone/email/nome do Profile.
    - `phone`/`email`: sobrescreve o do Profile (destino livre).
    - `ctx`: placeholders extras (`valor`, `link`, `detail`, ...). `nome`/`nome_completo` injetados.
    - overrides (`title`/`subject`/`media_url`/`gender`/`mail_template`) vencem o Template.
    - `body_md_override` (wave-2): corpo JÁ renderizado pelo caller, pula a leitura do
      `Template.body_md` e o `msgs.story_text()` (preserva storytelling RICO como
      `enrollment._selfie_story` que concatena link, faz append de saudação etc.). NÃO pula
      o `Trigger.active` — desativar evento ainda desliga.
    - `is_tts_override` (wave-2): força o modo TTS do WhatsApp independente do `Template.is_tts`.
      Útil quando o caller já sabe a regra (ex.: `_notify_resolution` lê `msgs.is_tts` direto).
      `None` = honra o Template (ou o catálogo in-memory, sem row).
    - `channels_override` (wave-2): força os canais (whatsapp/email) ignorando o Template.
      Útil quando o caller quer mandar SÓ um canal e não há Template cadastrado.
      `None` = usa `Template.channels` (ou default `whatsapp+email` sem row).
    """
    from users.roles import notifications as msgs

    data = _db.get(event)

    # ── Trigger.active=False desliga o evento (Victor, sem código) — vale mesmo com body_override ──
    if data is not None:
        trigger = _trigger_for(data)
        if trigger is not None and not trigger.active:
            logger.info("notify.event_inactive", event_key=event, caller="send_event")
            return None

    prof, nome, nome_completo, p_phone, p_email, p_gender, birth = _resolve_profile(
        user, profile
    )
    phone = phone or p_phone
    email = email or p_email
    gender = gender or p_gender

    # ── teor: 3 caminhos (override > DB.Template w/storytelling > catálogo in-memory) ──
    if body_md_override is not None:
        # Caller já compôs o texto (storytelling RICO + link appended, p.ex.). NÃO re-renderiza.
        body = body_md_override
        if is_tts_override is not None:
            is_tts = is_tts_override
        else:
            is_tts = data.is_tts if data is not None else msgs.is_tts(event)
        if channels_override is not None:
            channels = list(channels_override)
        elif data is not None:
            channels = list(data.channels) or list(_DEFAULT_CHANNELS)
        else:
            channels = list(_DEFAULT_CHANNELS)
        t_title = title or (data.title if data is not None else None)
        t_subject = (
            subject
            or (data.subject if data is not None else None)
            or "Sua matrícula — atualização"
        )
        t_media_url = media_url or (data.media_url if data is not None else None)
        t_media_type = media_type or (data.media_type if data is not None else None)
        t_mail_tpl = (
            mail_template
            or (data.mail_template if data is not None else None)
            or "default"
        )
    elif data is not None:
        # ctx base: nome/nome-completo + aliases legados (`name` = `nome`).
        render_ctx: dict = {
            "nome": nome or "tudo bem",
            "nome_completo": nome_completo or "tudo bem",
            "name": nome or "tudo bem",
        }
        if ctx:
            render_ctx.update(ctx)
        body = _db.render(data.body_md, render_ctx)
        is_tts = data.is_tts if is_tts_override is None else is_tts_override
        channels = (
            list(channels_override)
            if channels_override is not None
            else (list(data.channels) or list(_DEFAULT_CHANNELS))
        )
        t_title = title or data.title
        t_subject = subject or data.subject
        t_media_url = media_url or data.media_url
        t_media_type = media_type or data.media_type
        t_mail_tpl = mail_template or data.mail_template
        # storytelling: IA gera o teor (body_md é fallback). story_text honra o flag DB.
        if data.storytelling and body_md_override is None:
            body = msgs.story_text(
                event, name=nome or "tudo bem", fallback=body, age=msgs.age_from(birth)
            )
    else:
        # sem row no DB — catálogo in-memory (msgs.text/is_tts já são DB-com-fallback).
        render_ctx = {"nome": nome or "tudo bem", "name": nome or "tudo bem"}
        if ctx:
            render_ctx.update(ctx)
        body = msgs.text(event, **render_ctx)
        # storytelling via catálogo mesmo sem row no DB (marco especial → texto por IA, com
        # fallback pro teor fixo). Sem AI, story_text devolve o próprio body inalterado.
        body = msgs.story_text(
            event, name=nome or "tudo bem", fallback=body, age=msgs.age_from(birth)
        )
        is_tts = msgs.is_tts(event) if is_tts_override is None else is_tts_override
        channels = (
            list(channels_override)
            if channels_override is not None
            else list(_DEFAULT_CHANNELS)
        )
        t_title = title
        t_subject = subject
        t_media_url = media_url
        t_media_type = media_type
        t_mail_tpl = mail_template or "default"

    # ── flags por canal (TTS é MODO de entrega do WhatsApp, não canal paralelo) ──
    want_whatsapp = "whatsapp" in channels and bool(phone)
    want_email = "email" in channels and bool(email)
    want_tts = is_tts and want_whatsapp  # voice-note só faz sentido no WhatsApp
    if not want_whatsapp and not want_email:
        logger.warning("notify.event_no_recipient", event_key=event)
        return None

    return _send_iface.send(
        text=body,
        caller=f"event:{event}",
        phone=phone if want_whatsapp else None,
        email=email if want_email else None,
        title=t_title,
        subject=t_subject,
        whatsapp=want_whatsapp,
        email_channel=want_email,
        tts=want_tts,
        media_url=t_media_url,
        media_type=t_media_type,
        gender=gender if want_tts else None,
        mail_template=t_mail_tpl or "default",
        idempotency_key=idempotency_key,
        run_sync=run_sync,
    )


def _trigger_for(data) -> Trigger | None:
    """Trigger do Template (lookup pelo event; cache do Template não expõe PK — usa o slug)."""
    return Trigger.objects.filter(template__event=data.event).first()
