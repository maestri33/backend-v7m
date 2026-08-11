"""Gestão de Template/Trigger pelo staff — a fonte da COERÊNCIA do espelho local↔notify-server.

Cada operação = validar → gravar local → (se `NOTIFY_MODE=remote`) push atômico pro servidor →
invalidar o cache em memória, como UMA operação. Antes vivia esparramada em 5 corpos de handler do
`api/staff_notify` (mudar a regra do espelho exigia editar os 5, cada um repetindo
`if _remote(): with atomic(): write(); push()`); agora o router é adapter fino (auth + schema +
serialização). O servidor não tem PATCH → o PATCH aplica o parcial local e faz PUT FULL do resultado.
"""

from __future__ import annotations

import structlog
from django.db import transaction

from notify.interface import remote
from notify.interface import templates as _cache
from notify.models import Template, Trigger
from users.exceptions import DomainError, NotFound, ValidationError

logger = structlog.get_logger()

_VALID_CHANNELS = {"whatsapp", "email", "tts"}
_VALID_MEDIA = {"image", "video", "audio", "document"}


def _validate_channels(raw: str) -> str:
    parts = [p.strip() for p in (raw or "").split(",") if p.strip()]
    bad = [p for p in parts if p not in _VALID_CHANNELS]
    if bad:
        raise ValidationError(
            f"canais inválidos: {bad} (válido: {sorted(_VALID_CHANNELS)})",
            code="INVALID_CHANNELS",
        )
    return ",".join(parts) if parts else "whatsapp,email"


# ── espelho remoto (dual-write) ────────────────────────────────────────────────
def _server_template_payload(t: Template) -> dict:
    """Estado FULL do Template local no shape TemplateUpsertIn do servidor (que não tem PATCH)."""
    return dict(
        title=t.title,
        subject=t.subject,
        body_md=t.body_md,
        is_tts=t.is_tts,
        storytelling=t.storytelling,
        story_prompt=t.story_prompt,
        channels=t.channels,
        media_url=t.media_url,
        media_type=t.media_type,
        mail_template=t.mail_template,
        notes=t.notes,
    )


def _push_template(event: str, t: Template) -> None:
    """PUT full do Template resultante no servidor (dual-write)."""
    from notify.sdk import client

    remote.server_call(
        lambda: client.staff_put_template(event, _server_template_payload(t))
    )


def _push_trigger(event: str, tr: Trigger) -> None:
    """PUT do Trigger resultante no servidor (dual-write)."""
    from notify.sdk import client

    payload = dict(
        fires_on=tr.fires_on or "",
        source=tr.source,
        delay_minutes=tr.delay_minutes,
        active=tr.active,
    )
    remote.server_call(lambda: client.staff_put_trigger(event, payload))


def _push_delete(event: str) -> None:
    """DELETE no servidor. 404 lá = já não existia — delete é idempotente, espelho segue coeso."""
    from notify.sdk import client

    def _delete():
        try:
            client.staff_delete_template(event)
        except client.NotifyServerError as exc:
            if exc.status_code == 404:
                # kwarg não pode se chamar `event` (posicional do structlog)
                logger.info("staff_notify.remote_delete_absent", event_slug=event)
                return
            raise

    remote.server_call(_delete)


def _apply(event, write, push):
    """Grava local e, em modo remote, espelha no servidor ATOMICAMENTE — falha do push desfaz a
    escrita local (espelho coeso). `push` recebe o resultado do `write`."""
    if remote.is_remote():
        with transaction.atomic():
            result = write()
            push(result)
            return result
    return write()


# ── operações públicas (o router `api/staff_notify` chama estas) ───────────────
def upsert_template(
    event: str,
    *,
    title,
    subject,
    body_md,
    is_tts,
    storytelling,
    story_prompt,
    channels,
    media_url,
    media_type,
    mail_template,
    notes,
) -> Template:
    """Cria ou atualiza o Template do `event` (upsert). `body_md` obrigatório; invalida o cache."""
    if not body_md.strip():
        raise ValidationError("body_md não pode ser vazio.", code="EMPTY_BODY")
    if media_type and media_type not in _VALID_MEDIA:
        raise ValidationError(
            f"media_type inválido (válido: {sorted(_VALID_MEDIA)})",
            code="INVALID_MEDIA_TYPE",
        )
    defaults = dict(
        title=title,
        subject=subject,
        body_md=body_md,
        is_tts=is_tts,
        storytelling=storytelling,
        story_prompt=story_prompt,
        channels=_validate_channels(channels),
        media_url=media_url,
        media_type=media_type,
        mail_template=mail_template or "default",
        notes=notes,
    )

    def _write() -> Template:
        t, _ = Template.objects.update_or_create(event=event, defaults=defaults)
        _cache.invalidate(event)  # cache em memória reflete a edição na hora
        return t

    return _apply(event, _write, lambda t: _push_template(event, t))


def upsert_trigger(event: str, *, fires_on, source, delay_minutes, active) -> Trigger:
    """Cria ou atualiza o Trigger do Template `event`. Template inexistente → 404."""
    t = Template.objects.filter(event=event).first()
    if t is None:
        raise NotFound("Template não encontrado.", code="TEMPLATE_NOT_FOUND")
    defaults = dict(
        fires_on=fires_on,
        source=source,
        delay_minutes=max(0, int(delay_minutes)),
        active=active,
    )

    def _write() -> Trigger:
        tr, _ = Trigger.objects.update_or_create(template=t, defaults=defaults)
        _cache.invalidate(event)
        return tr

    return _apply(event, _write, lambda tr: _push_trigger(event, tr))


def patch_template(event: str, fields: dict) -> Template:
    """Atualização PARCIAL: só os campos em `fields` (o model_dump exclude_unset do payload). `channels`
    é revalidado; `body_md` vazio → 422. O servidor não tem PATCH → em remote, PUT FULL do resultante."""
    t = Template.objects.filter(event=event).first()
    if t is None:
        raise NotFound("Template não encontrado.", code="TEMPLATE_NOT_FOUND")
    if fields.get("body_md") is not None and not fields["body_md"].strip():
        raise ValidationError("body_md não pode ser vazio.", code="EMPTY_BODY")
    if (
        fields.get("media_type") is not None
        and fields["media_type"] not in _VALID_MEDIA
    ):
        raise ValidationError(
            f"media_type inválido (válido: {sorted(_VALID_MEDIA)})",
            code="INVALID_MEDIA_TYPE",
        )
    channels = (
        _validate_channels(fields["channels"])
        if fields.get("channels") is not None
        else None
    )

    def _write() -> Template:
        changed = False
        for k, v in fields.items():
            if k == "channels":
                if t.channels != channels:
                    t.channels = channels
                    changed = True
            elif getattr(t, k) != v:
                setattr(t, k, v)
                changed = True
        if changed:
            t.save()
            _cache.invalidate(event)
        return t

    return _apply(event, _write, lambda t: _push_template(event, t))


def delete_template(event: str) -> None:
    """APAGA o Template (e o Trigger em cascata — OneToOne). Em remote, DELETE espelhado (404 = no-op)."""
    t = Template.objects.filter(event=event).first()
    if t is None:
        raise NotFound("Template não encontrado.", code="TEMPLATE_NOT_FOUND")

    def _write() -> None:
        t.delete()
        _cache.invalidate(event)

    _apply(event, _write, lambda _: _push_delete(event))


def restore_seed(event: str) -> Template:
    """Recarrega UM Template do `notify/seed/templates.md` (sobrescreve o do DB). Fora do seed → 404."""
    from pathlib import Path

    from notify.seed import io as seed_io

    path = Path(__file__).resolve().parents[1] / "seed" / "templates.md"
    if not path.exists():
        # erro de deploy (seed ausente no servidor), não do cliente → 500 com code próprio.
        err = DomainError(f"seed .md ausente: {path}", code="SEED_FILE_MISSING")
        err.status = 500
        raise err
    specs = {s.event: s for s in seed_io.parse(path.read_text(encoding="utf-8"))}
    spec = specs.get(event)
    if spec is None:
        raise NotFound(
            f"evento '{event}' não está no seed .md", code="EVENT_NOT_IN_SEED"
        )
    fields = dict(
        body_md=spec.body_md,
        is_tts=spec.is_tts,
        storytelling=spec.storytelling,
        channels=spec.channels,
        title=spec.title,
        subject=spec.subject,
        media_url=spec.media_url,
        media_type=spec.media_type,
        mail_template=spec.mail_template,
        story_prompt=spec.story_prompt,
    )

    def _write() -> Template:
        t, _ = Template.objects.update_or_create(event=event, defaults=fields)
        _cache.invalidate(event)
        return t

    return _apply(event, _write, lambda t: _push_template(event, t))
