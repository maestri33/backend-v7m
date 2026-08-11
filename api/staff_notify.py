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

`/history` é proxy do GET /v1/notifications do notify-server; as MUTAÇÕES de Template/Trigger fazem
dual-write (espelho local + push pro servidor, atômico) — preview/GET/storytelling seguem locais.
"""

from __future__ import annotations

from ninja import Router, Schema

from api.auth import require_superuser
from notify.interface import remote
from notify.interface import staff as notify_staff
from notify.models import Template, Trigger
from users.exceptions import NotFound

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


# ── notify-server ───────────────────────────────────────────────────────────────
# O wrapper de chamada ao servidor mora em `notify.interface.remote`; o dual-write de Template/Trigger
# virou o módulo `notify.interface.staff` (importado como `notify_staff`) — o router aqui é adapter fino.


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
    from notify.sdk import client

    # A verdade dos envios mora no notify-server (a tabela local de Notification foi aposentada
    # junto com o adapter local). O /history é sempre proxy do GET /v1/notifications.
    rows = remote.server_call(
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
    t = notify_staff.upsert_template(event, **payload.model_dump())
    return _template_out(t)


@router.put("/templates/{event}/trigger", response=TriggerOut)
def upsert_trigger(request, event: str, payload: TriggerUpsertIn):
    """Cria ou atualiza o Trigger do Template `event`. `active=false` DESLIGA o evento (send_event
    retorna None sem disparar) — o "interruptor" do Victor sem código. Template inexistente → 404."""
    require_superuser(request.auth)
    tr = notify_staff.upsert_trigger(event, **payload.model_dump())
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
    t = notify_staff.patch_template(event, payload.model_dump(exclude_unset=True))
    return _template_out(t)


# ── DELETE (remove o Template + Trigger em cascata) ───────────────────────────
@router.delete("/templates/{event}")
def delete_template(request, event: str):
    """APAGA o Template (e o Trigger em cascata — OneToOne). Use com cuidado — o seed não vai
    recriar automaticamente (use POST /restore-seed pra isso).

    NOTIFY_MODE=local: a próxima chamada de `send_event(event)` cai no catálogo in-memory
    legado (`users.roles.notifications`). NOTIFY_MODE=remote: NÃO há esse fallback — o
    notify-server não conhece o catálogo do monólito, então o evento simplesmente para de
    disparar (404 tratado como no-op) até alguém restaurar o Template (review adversarial:
    esse fallback só existe hoje pra 1 evento de baixo uso, `enrollment.concluded_referral`,
    já quebrado por um bug pré-existente não relacionado — extra= inválido em
    `student/signals.py`). Confirme os 2 lados (local + notify-server) antes de apagar em prod."""
    require_superuser(request.auth)
    notify_staff.delete_template(event)
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
    t = notify_staff.restore_seed(event)
    return _template_out(t)
