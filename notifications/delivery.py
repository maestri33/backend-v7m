from __future__ import annotations

import uuid

import structlog
from django.core.exceptions import ValidationError

from notifications.models import NotificationTemplate, validate_media_url

logger = structlog.get_logger()


def _name(value: str | None, *, full: bool = False) -> str | None:
    clean = " ".join((value or "").split())
    return clean if full else (clean.split()[0] if clean else None)


def _resolve_profile(user, profile):
    if profile is None and user:
        from users.profiles import interface as profiles

        profile = profiles.find_by_external_id(str(getattr(user, "external_id", user)))
    if profile is None:
        return None, None, None, None, None, None
    raw_name = getattr(profile, "name", None)
    return (
        _name(raw_name),
        _name(raw_name, full=True),
        getattr(profile, "phone", None) or None,
        getattr(profile, "email", None) or None,
        getattr(profile, "gender", None) or None,
        getattr(profile, "birth_date", None),
    )


def _render(value: str | None, context: dict, *, event: str) -> str | None:
    if value is None:
        return None
    try:
        return value.format_map(context)
    except KeyError as exc:
        raise ValidationError(
            f"O evento {event} exige o campo {exc.args[0]!r} no contexto."
        ) from exc
    except (ValueError, AttributeError) as exc:
        raise ValidationError(f"O conteúdo do evento {event} é inválido.") from exc


def send_event(
    event: str,
    *,
    user=None,
    profile=None,
    phone: str | None = None,
    email: str | None = None,
    ctx: dict | None = None,
    extra: dict | None = None,
    gender: str | None = None,
    idempotency_key: str | None = None,
    run_sync: bool = False,
    channels_override: tuple[str, ...] | list[str] | None = None,
) -> str | None:
    try:
        template = NotificationTemplate.objects.get(event=event)
    except NotificationTemplate.DoesNotExist as exc:
        raise ValidationError(f"Evento de notificação desconhecido: {event}.") from exc
    if not template.active:
        return None

    nome, nome_completo, profile_phone, profile_email, profile_gender, birth_date = (
        _resolve_profile(user, profile)
    )
    phone = phone or profile_phone
    email = email or profile_email
    if not phone and not email:
        logger.warning("notifications.no_recipient", event_key=event)
        return None

    context = dict(ctx or {})
    context.update(extra or {})
    context_name = str(context.get("nome") or context.get("name") or nome or "")
    context_full_name = str(
        context.get("nome-completo")
        or context.get("nome_completo")
        or nome_completo
        or context_name
    )
    context["nome"] = str(context.get("nome") or context_name)
    context["name"] = str(context.get("name") or context_name)
    context["nome-completo"] = str(context.get("nome-completo") or context_full_name)
    context["nome_completo"] = str(context.get("nome_completo") or context_full_name)
    reason = str(context.get("reason") or "").strip()
    context.setdefault("reason_text", f" Motivo: {reason}." if reason else "")
    channels = tuple(channels_override) if channels_override is not None else template.channel_names
    selected_phone = phone if "whatsapp" in channels else None
    selected_email = email if "email" in channels else None
    if not selected_phone and not selected_email:
        logger.warning(
            "notifications.no_recipient_for_channels",
            event_key=event,
            channels=channels,
        )
        return None

    rendered_body = _render(template.body, context, event=event)
    from notifications.storytelling import generate_story

    rendered_body = generate_story(
        template,
        name=str(context.get("name") or context.get("nome") or ""),
        birth_date=birth_date,
        fallback=rendered_body or "",
    )
    rendered_title = _render(template.title, context, event=event) or None
    rendered_subject = _render(template.subject, context, event=event) or None
    validate_media_url(template.media_url)

    from integrations.notify.delivery import send

    return send(
        text=rendered_body or "",
        caller=event,
        phone=selected_phone,
        email=selected_email,
        title=rendered_title,
        subject=rendered_subject,
        whatsapp="whatsapp" in channels and bool(phone),
        email_channel="email" in channels and bool(email),
        tts=template.is_tts and bool(selected_phone),
        media_url=template.media_url,
        media_type=template.media_type or None,
        gender=gender or profile_gender,
        mail_template=template.mail_template,
        idempotency_key=idempotency_key,
        run_sync=run_sync,
    )


def send_adhoc(
    *,
    message: str,
    to_user: str | None = None,
    phone: str | None = None,
    email: str | None = None,
    subject: str | None = None,
    channels: list[str] | None = None,
    caller: str = "tools.send",
) -> str | None:
    from users.exceptions import NotFound, ValidationError as DomainValidationError

    message = (message or "").strip()
    if not message:
        raise DomainValidationError("Mensagem não pode ser vazia.", code="MISSING_FIELD")
    if to_user:
        from users.profiles import interface as profiles

        profile = profiles.find_by_external_id(to_user)
        if profile is None:
            raise NotFound("Usuário não encontrado.", code="USER_NOT_FOUND")
        phone = phone or profile.phone or None
        email = email or profile.email or None
    phone = (phone or "").strip() or None
    email = (email or "").strip().lower() or None
    if not phone and not email:
        raise DomainValidationError("Informe ao menos um destino.", code="MISSING_FIELD")

    requested_channels = {
        channel.strip().lower() for channel in (channels or ["whatsapp", "email"])
    }
    unknown_channels = requested_channels - {"whatsapp", "email"}
    if unknown_channels:
        raise DomainValidationError(
            f"Canal desconhecido: {', '.join(sorted(unknown_channels))}.",
            code="INVALID_CHANNEL",
        )
    available_channels = {
        channel
        for channel in requested_channels
        if (channel == "whatsapp" and phone) or (channel == "email" and email)
    }
    if not available_channels:
        raise DomainValidationError(
            "Nenhum canal possui destino válido.", code="MISSING_FIELD"
        )
    event = caller if NotificationTemplate.objects.filter(event=caller).exists() else "tools.adhoc"
    return send_event(
        event,
        phone=phone,
        email=email,
        ctx={"message": message, "subject": subject or ""},
        channels_override=sorted(available_channels),
        idempotency_key=str(uuid.uuid4()),
    )
