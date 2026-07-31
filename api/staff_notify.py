"""Sub-router `staff/notify` — gestor de notificações do staff (superuser).

Quatro frentes (Victor 2026-07-02):
 1. **Envio avulso** (POST /notify) — WhatsApp/e-mail a um usuário OU destino livre.
 2. **Histórico** (GET /notify/history) — o que foi enviado (audit `Notification`), com filtros.
 3. **CRUD de Template/Trigger** (GET/PUT/PATCH/DELETE /notify/templates[/...]) — editar o teor,
    flags, canais, mídia, gatilho SEM código. PUT faz upsert completo, PATCH faz parcial.
 4. **DX** (GET /events, GET /stats, POST /preview, POST /test, POST /restore-seed) — utilidades
    pro frontend staff (autocomplete de eventos, dashboard, preview de render, restauração do seed).

Tudo exige SUPERUSER (`require_superuser`). `event` é o slug do Template (único, estável).
A fonte de verdade é o DB (`notify.Template`); o `notify/seed/templates.md` é só o seed inicial
(default: cria o que falta; `--force` sobrescreve). Edições via este CRUD prevalecem sobre o seed.

Fase 2 (`NOTIFY_MODE=remote`): `/history` vira proxy do GET /v1/notifications do notify-server e
as MUTAÇÕES de Template/Trigger fazem dual-write (local + push pro servidor, atômico) — o espelho
local fica coeso, então rollback de modo é seguro e preview/GET/storytelling seguem locais.
"""

from __future__ import annotations

import structlog
from django.conf import settings
from ninja import Router, Schema

from api.auth import require_superuser
from notify.interface import templates as _db_cache
from notify.models import Notification, Template, Trigger
from users.exceptions import DomainError, IntegrationError, NotFound, ValidationError

logger = structlog.get_logger()

router = Router(tags=["notify"])


# ── envio avulso (movido de staff.py) ──────────────────────────────────────────
class StaffNotifyIn(Schema):
    user_external_id: str | None = None
    phone: str | None = None
    email: str | None = None
    subject: str | None = None
    message: str
    channels: list[str] | None = None  # subconjunto de {"whatsapp","email"}


@router.post("", url_name="staff-notify")
def staff_notify(request, payload: StaffNotifyIn):
    """Envia uma notificação avulsa (whatsapp e/ou e-mail) a um USUÁRIO (`user_external_id`, herda
    phone/email do Profile) OU a um destino LIVRE (`phone`/`email` sem cadastro). `channels` opcional
    (default: todos com destino). Valida na borda: mensagem não-vazia + pelo menos um destino.
    Devolve o `external_id` da notificação enfileirada."""
    require_superuser(request.auth)
    from notify.interface.send import send_adhoc

    external_id = send_adhoc(
        message=payload.message,
        to_user=payload.user_external_id,
        phone=payload.phone,
        email=payload.email,
        subject=payload.subject,
        channels=payload.channels,
        caller="staff.notify",
    )
    return {"external_id": external_id}


# ── histórico (o que foi enviado) ──────────────────────────────────────────────
class NotificationOut(Schema):
    external_id: str
    caller: str | None
    recipient_phone: str | None
    recipient_email: str | None
    title: str | None
    subject: str | None
    text: str
    want_whatsapp: bool
    want_email: bool
    want_tts: bool
    whatsapp_status: str | None
    email_status: str | None
    tts_status: str | None
    whatsapp_error: str | None
    email_error: str | None
    tts_error: str | None
    attempts: int
    created_at: str


# ── NOTIFY_MODE=remote: proxy do /history (a verdade dos ENVIOS mora no notify-server). ────
# O catálogo de Templates é do supletivo (fonte única, local) — não há mais dual-write ao servidor.
def _remote() -> bool:
    """Lê a flag a cada chamada (rollback = trocar NOTIFY_MODE + restart, sem redeploy)."""
    return settings.NOTIFY_MODE == "remote"


def _server_call(fn):
    """Chama o notify-server; falha (HTTP >=400 ou rede) vira 502 `NOTIFY_SERVER_DOWN` no
    envelope padrão. Dentro do `transaction.atomic`, a exceção desfaz a escrita local."""
    import httpx

    from notify.sdk import client

    try:
        return fn()
    except (client.NotifyServerError, httpx.HTTPError) as exc:
        raise IntegrationError(
            "notify-server indisponível — tente novamente.",
            code="NOTIFY_SERVER_DOWN",
        ) from exc


@router.get("/history", response=list[NotificationOut])
def notify_history(
    request,
    caller: str | None = None,
    whatsapp_status: str | None = None,
    email_status: str | None = None,
    tts_status: str | None = None,
    limit: int = 100,
):
    """Notificações enviadas (audit `Notification`), mais recentes primeiro. Filtros opcionais por
    `caller` (ex.: `event:lead.paid`, `staff.notify`) e por status de cada canal (pending/sent/failed/
    skipped). `limit` máx 500. Modo remote: proxy do notify-server (a verdade dos envios mora lá)."""
    require_superuser(request.auth)
    limit = max(1, min(int(limit), 500))
    if _remote():
        from notify.sdk import client

        rows = _server_call(
            lambda: client.get_notifications(
                caller=caller,
                whatsapp_status=whatsapp_status,
                email_status=email_status,
                tts_status=tts_status,
                limit=limit,
            )
        )
        return [
            NotificationOut(
                external_id=str(r.get("external_id") or ""),
                caller=r.get("caller"),
                recipient_phone=r.get("recipient_phone"),
                recipient_email=r.get("recipient_email"),
                title=r.get("title"),
                subject=r.get("subject"),
                text=r.get("text") or "",
                want_whatsapp=bool(r.get("want_whatsapp")),
                want_email=bool(r.get("want_email")),
                want_tts=bool(r.get("want_tts")),
                whatsapp_status=r.get("whatsapp_status"),
                email_status=r.get("email_status"),
                tts_status=r.get("tts_status"),
                whatsapp_error=r.get("whatsapp_error"),
                email_error=r.get("email_error"),
                tts_error=r.get("tts_error"),
                attempts=int(r.get("attempts") or 0),
                created_at=r.get("created_at") or "",
            )
            for r in rows
        ]
    qs = Notification.objects.order_by("-created_at")
    if caller:
        qs = qs.filter(caller=caller)
    if whatsapp_status:
        qs = qs.filter(whatsapp_status=whatsapp_status)
    if email_status:
        qs = qs.filter(email_status=email_status)
    if tts_status:
        qs = qs.filter(tts_status=tts_status)
    return [
        NotificationOut(
            external_id=str(n.external_id),
            caller=n.caller,
            recipient_phone=n.recipient_phone,
            recipient_email=n.recipient_email,
            title=n.title,
            subject=n.subject,
            text=n.text,
            want_whatsapp=n.want_whatsapp,
            want_email=n.want_email,
            want_tts=n.want_tts,
            whatsapp_status=n.whatsapp_status,
            email_status=n.email_status,
            tts_status=n.tts_status,
            whatsapp_error=n.whatsapp_error,
            email_error=n.email_error,
            tts_error=n.tts_error,
            attempts=n.attempts,
            created_at=n.created_at.isoformat(),
        )
        for n in qs[:limit]
    ]


# ── CRUD de Template + Trigger ─────────────────────────────────────────────────
class TriggerOut(Schema):
    fires_on: str
    source: str | None
    delay_minutes: int
    active: bool


class TemplateOut(Schema):
    event: str
    external_id: str
    title: str | None
    subject: str | None
    body_md: str
    is_tts: bool
    storytelling: bool
    story_prompt: str | None
    channels: str
    media_url: str | None
    media_type: str | None
    mail_template: str
    notes: str | None
    updated_at: str
    trigger: TriggerOut | None


class TemplateUpsertIn(Schema):
    title: str | None = None
    subject: str | None = None
    body_md: str
    is_tts: bool = False
    storytelling: bool = False
    story_prompt: str | None = None
    channels: str = "whatsapp,email"
    media_url: str | None = None
    media_type: str | None = None
    mail_template: str = "default"
    notes: str | None = None


class TriggerUpsertIn(Schema):
    fires_on: str = ""
    source: str | None = None
    delay_minutes: int = 0
    active: bool = True


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


def _trigger_out(t: Trigger | None) -> TriggerOut | None:
    if t is None:
        return None
    return TriggerOut(
        fires_on=t.fires_on or "",
        source=t.source or None,
        delay_minutes=t.delay_minutes,
        active=t.active,
    )


def _template_out(t: Template) -> TemplateOut:
    tr = Trigger.objects.filter(template=t).first()
    return TemplateOut(
        event=t.event,
        external_id=str(t.external_id),
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
        updated_at=t.updated_at.isoformat(),
        trigger=_trigger_out(tr),
    )


@router.get("/templates", response=list[TemplateOut])
def list_templates(request):
    """Todos os Templates (catálogo de eventos) + seu Trigger (ativo/fires_on/delay)."""
    require_superuser(request.auth)
    return [_template_out(t) for t in Template.objects.order_by("event")]


# ── stats ANTES de /templates/{event} (evita que "stats" seja capturado como event slug) ─────
class TemplateStatsOut(Schema):
    total: int
    active: int
    inactive: int
    with_tts: int
    with_storytelling: int
    with_media: int
    by_channel: dict[str, int]  # {"whatsapp": N, "email": M, ...}


@router.get("/templates/stats", response=TemplateStatsOut)
def template_stats(request):
    """Dashboard do gestor: contagem por flag/canal. Sem paginação (catálogo é pequeno)."""
    require_superuser(request.auth)
    qs = Template.objects.all()
    total = qs.count()
    active = qs.filter(trigger__active=True).count()
    inactive = total - active
    with_tts = qs.filter(is_tts=True).count()
    with_story = qs.filter(storytelling=True).count()
    with_media = qs.exclude(media_url__isnull=True).exclude(media_url="").count()
    by_channel: dict[str, int] = {"whatsapp": 0, "email": 0, "tts": 0}
    for t in qs:
        for ch in t.channel_list:
            by_channel[ch] = by_channel.get(ch, 0) + 1
    return TemplateStatsOut(
        total=total,
        active=active,
        inactive=inactive,
        with_tts=with_tts,
        with_storytelling=with_story,
        with_media=with_media,
        by_channel=by_channel,
    )


@router.get("/templates/{event}", response=TemplateOut)
def get_template(request, event: str):
    """Detalhe de um Template + Trigger."""
    require_superuser(request.auth)
    t = Template.objects.filter(event=event).first()
    if t is None:
        raise NotFound("Template não encontrado.", code="TEMPLATE_NOT_FOUND")
    return _template_out(t)


@router.put("/templates/{event}", response=TemplateOut)
def upsert_template(request, event: str, payload: TemplateUpsertIn):
    """Cria ou atualiza o Template do `event` (upsert). `body_md` obrigatório (Markdown). Edição aqui
    invalida o cache em memória → próxima chamada lê o teor novo. Não deleta: pra desligar um evento,
    use o Trigger (`PUT .../trigger` com `active=false`)."""
    require_superuser(request.auth)
    if not payload.body_md.strip():
        raise ValidationError("body_md não pode ser vazio.", code="EMPTY_BODY")
    if payload.media_type and payload.media_type not in _VALID_MEDIA:
        raise ValidationError(
            f"media_type inválido (válido: {sorted(_VALID_MEDIA)})",
            code="INVALID_MEDIA_TYPE",
        )
    channels = _validate_channels(payload.channels)
    defaults = dict(
        title=payload.title,
        subject=payload.subject,
        body_md=payload.body_md,
        is_tts=payload.is_tts,
        storytelling=payload.storytelling,
        story_prompt=payload.story_prompt,
        channels=channels,
        media_url=payload.media_url,
        media_type=payload.media_type,
        mail_template=payload.mail_template or "default",
        notes=payload.notes,
    )

    def _write() -> Template:
        t, _ = Template.objects.update_or_create(event=event, defaults=defaults)
        _db_cache.invalidate(event)  # cache em memória reflete a edição na hora
        return t

    t = _write()
    return _template_out(t)


@router.put("/templates/{event}/trigger", response=TriggerOut)
def upsert_trigger(request, event: str, payload: TriggerUpsertIn):
    """Cria ou atualiza o Trigger do Template `event`. `active=false` DESLIGA o evento (send_event
    retorna None sem disparar) — o "interruptor" do Victor sem código. Template inexistente → 404."""
    require_superuser(request.auth)
    t = Template.objects.filter(event=event).first()
    if t is None:
        raise NotFound("Template não encontrado.", code="TEMPLATE_NOT_FOUND")
    defaults = dict(
        fires_on=payload.fires_on,
        source=payload.source,
        delay_minutes=max(0, int(payload.delay_minutes)),
        active=payload.active,
    )

    def _write() -> Trigger:
        tr, _ = Trigger.objects.update_or_create(template=t, defaults=defaults)
        _db_cache.invalidate(event)
        return tr

    tr = _write()
    return _trigger_out(tr)


# ── PATCH (atualização PARCIAL: só os campos enviados) ─────────────────────────
class TemplatePatchIn(Schema):
    """PATCH: TODOS os campos opcionais — só atualiza o que vier. Use quando o staff ajusta UM campo
    sem reenviar o body inteiro (ex.: desligar TTS sem tocar no body_md)."""

    title: str | None = None
    subject: str | None = None
    body_md: str | None = None
    is_tts: bool | None = None
    storytelling: bool | None = None
    story_prompt: str | None = None
    channels: str | None = None
    media_url: str | None = None
    media_type: str | None = None
    mail_template: str | None = None
    notes: str | None = None


@router.patch("/templates/{event}", response=TemplateOut)
def patch_template(request, event: str, payload: TemplatePatchIn):
    """Atualização PARCIAL do Template. Só altera os campos enviados no payload. body_md vazio → 422."""
    require_superuser(request.auth)
    t = Template.objects.filter(event=event).first()
    if t is None:
        raise NotFound("Template não encontrado.", code="TEMPLATE_NOT_FOUND")
    if payload.body_md is not None and not payload.body_md.strip():
        raise ValidationError("body_md não pode ser vazio.", code="EMPTY_BODY")
    if payload.media_type is not None and payload.media_type not in _VALID_MEDIA:
        raise ValidationError(
            f"media_type inválido (válido: {sorted(_VALID_MEDIA)})",
            code="INVALID_MEDIA_TYPE",
        )
    if payload.channels is not None:
        channels = _validate_channels(payload.channels)
    else:
        channels = None

    def _write() -> None:
        changed = False
        data = payload.model_dump(exclude_unset=True)
        for k, v in data.items():
            if k == "channels":
                if t.channels != channels:
                    t.channels = channels
                    changed = True
            elif getattr(t, k) != v:
                setattr(t, k, v)
                changed = True
        if changed:
            t.save()
            _db_cache.invalidate(event)

    _write()
    return _template_out(t)


# ── DELETE (remove o Template + Trigger em cascata) ───────────────────────────
@router.delete("/templates/{event}")
def delete_template(request, event: str):
    """APAGA o Template (e o Trigger em cascata — OneToOne). Use com cuidado — o seed não vai
    recriar automaticamente (use POST /restore-seed pra isso).

    Sem row no DB, a próxima chamada de `send_event(event)` cai no catálogo in-memory legado
    (`users.roles.notifications`), tanto em modo local quanto remote (o teor é sempre resolvido
    aqui antes de ir ao notify-server)."""
    require_superuser(request.auth)
    t = Template.objects.filter(event=event).first()
    if t is None:
        raise NotFound("Template não encontrado.", code="TEMPLATE_NOT_FOUND")

    def _write() -> None:
        t.delete()
        _db_cache.invalidate(event)

    _write()
    return {"deleted": event}


# ── DX pro frontend ────────────────────────────────────────────────────────────
class EventCatalogItem(Schema):
    """Item do catálogo de eventos conhecidos: tudo o que o app notifica."""

    event: str
    has_template: bool  # já existe row no DB?
    has_in_memory: bool  # tem texto no catálogo Python (legado)?
    active: bool | None  # Trigger.active (None se não tem Trigger)


@router.get("/events", response=list[EventCatalogItem])
def list_events(request):
    """Catálogo COMPLETO de eventos conhecidos: DB ∪ in-memory. Útil pra dropdown do form (staff
    escolhe qual evento editar) — não precisa adivinhar slug. `has_template`/`has_in_memory` dizem
    a fonte do teor."""
    require_superuser(request.auth)
    from users.roles import notifications as msgs

    # 1. Eventos do catálogo in-memory (legado — fonte dos textos antes do DB).
    in_memory = (
        set(msgs._MESSAGES.keys())
        | set(msgs._TTS_EVENTS)
        | set(getattr(msgs, "_STORY_EVENTS", set()))
    )

    # 2. Eventos do DB (Templates + seus Triggers).
    db_events = {t.event: t for t in Template.objects.all()}
    triggers = {
        tr.template_id: tr for tr in Trigger.objects.select_related("template").all()
    }

    out: list[EventCatalogItem] = []
    seen: set[str] = set()
    for ev, tpl in sorted(db_events.items()):
        tr = triggers.get(tpl.id)
        out.append(
            EventCatalogItem(
                event=ev,
                has_template=True,
                has_in_memory=ev in in_memory,
                active=tr.active if tr else None,
            )
        )
        seen.add(ev)
    for ev in sorted(in_memory):
        if ev in seen:
            continue
        out.append(
            EventCatalogItem(
                event=ev,
                has_template=False,
                has_in_memory=True,
                active=None,
            )
        )
    return out


class PreviewIn(Schema):
    """ctx opcional p/ render. Sem `name`, o `nome` cai em "tudo bem"."""

    ctx: dict | None = None


class PreviewOut(Schema):
    event: str
    body_md: str  # original
    rendered: str  # após regex render
    is_tts: bool
    storytelling: bool
    channels: list[str]
    story_rendered: str | None = None  # só se storytelling=True (simulação barata)


@router.post("/templates/{event}/preview", response=PreviewOut)
def preview_template(request, event: str, payload: PreviewIn):
    """Renderiza o `body_md` com o ctx enviado (sem chamar IA). Devolve o TEXTO que sairia pro
    destinatário. Crítico pro staff ver antes de salvar — preview do WhatsApp.

    Não chama `send_event` (não despacha de verdade). Para um envio real de teste, use
    POST /test."""
    require_superuser(request.auth)
    from notify.interface import templates as _db_cache

    data = _db_cache.get(event)
    if data is None:
        raise NotFound("Template não encontrado.", code="TEMPLATE_NOT_FOUND")
    ctx = {"nome": "tudo bem", "nome_completo": "tudo bem", "name": "tudo bem"}
    if payload.ctx:
        ctx.update(payload.ctx)
    rendered = _db_cache.render(data.body_md, ctx)
    return PreviewOut(
        event=event,
        body_md=data.body_md,
        rendered=rendered,
        is_tts=data.is_tts,
        storytelling=data.storytelling,
        channels=list(data.channels),
        # story_rendered fica None — renderização real chama LLM (cara); aqui só indicamos que
        # o evento dispara storytelling (o front pode avisar "esse evento gera com IA").
        story_rendered=None,
    )


class TestSendIn(Schema):
    """Disparo REAL do evento PROPRIO STAFF LOGADO. Channels default do Template."""

    channels: list[str] | None = (
        None  # subconjunto de {"whatsapp","email"}; default = Template
    )
    ctx: dict | None = None


@router.post("/templates/{event}/test")
def test_template(request, event: str, payload: TestSendIn):
    """Envia a notificação do evento PROPRIO STAFF LOGADO (preview real). Sem destinatário externo —
    o canal usa o phone/email do staff. SEM `body_md_override` (queremos ver o que sai DO Template)."""
    require_superuser(request.auth)
    from notify.interface.events import send_event

    try:
        ext = send_event(
            event,
            user=str(request.auth.external_id),
            ctx=payload.ctx,
            channels_override=tuple(payload.channels) if payload.channels else None,
            # G19: SEM idempotency_key. A key era estável (event+staff), então o 2º clique em "testar"
            # retornava a notificação anterior e NÃO enviava — o preview parava de funcionar. Preview é
            # pra ver o resultado a CADA clique; idempotência não faz sentido aqui (não é evento de
            # negócio com risco de duplicação, é o próprio staff testando).
            run_sync=True,  # síncrono pra o staff ver o resultado AGORA
        )
    except KeyError:
        # evento sem Template no DB e ausente do catálogo in-memory: msgs.text() levanta KeyError.
        # Vira 404 EVENT_NOT_FOUND (senão o handler genérico devolveria 500).
        ext = None
    if ext is None:
        raise NotFound(
            f"evento '{event}' não existe (nem Template, nem catálogo in-memory).",
            code="EVENT_NOT_FOUND",
        )
    return {"external_id": ext}


@router.post("/templates/{event}/restore-seed")
def restore_from_seed(request, event: str):
    """Recarrega UM Template do `notify/seed/templates.md` (sobrescreve o do DB). Útil quando o staff
    editou errado e quer voltar ao teor original. Se o evento não está no seed → 404."""
    require_superuser(request.auth)
    from pathlib import Path

    from notify.seed import io as seed_io

    path = Path(__file__).resolve().parents[1] / "notify" / "seed" / "templates.md"
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
        _db_cache.invalidate(event)
        return t

    t = _write()
    return _template_out(t)
