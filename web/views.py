"""Views do funil web — casca fina (HTMX) sobre os services de `users/` (in-process).

Padrão: cada PASSO tem página própria (GET, guardada pelo gate) + endpoints HTMX (POST/poll)
que devolvem parciais. Sucesso que muda de passo → `HX-Redirect` (página cheia, URL certa).
`DomainError` → parcial de feedback com a mensagem; `WRONG_STATUS` → corrige a rota (gate)."""

from __future__ import annotations

import functools

from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from users.auth import service as auth_iface
from users.auth.models import User
from users.consent import PROMOTER_CONTRACT
from users.exceptions import DomainError
from users.profiles import interface as profiles
from users.roles import interface as roles
from users.roles.candidate import service as candidate_iface
from users.roles.promoter import service as promoter_iface
from users.roles.training import service as training_iface
from web import flow

# ── helpers ──────────────────────────────────────────────────────────────────


def _hx_redirect(url: str) -> HttpResponse:
    resp = HttpResponse(status=204)
    resp["HX-Redirect"] = url
    return resp


def _go_current(request) -> HttpResponse:
    """Manda o cliente pra rota do passo REAL (gate A3)."""
    url = flow.step_url(flow.current_step(request))
    if request.headers.get("HX-Request"):
        return _hx_redirect(url)
    return redirect(url)


def _feedback(request, *, tone: str, title: str, text: str, shake: bool = False):
    return render(
        request,
        "web/partials/feedback.html",
        {"tone": tone, "title": title, "text": text, "shake": shake},
    )


# mensagens amigáveis por `code` de domínio (o resto cai no genérico).
_ERROR_TEXT = {
    "PHONE_INVALID": (
        "Confere esse número?",
        "Esse telefone não parece válido. Digite o DDD + número do seu WhatsApp.",
    ),
    "CPF_INVALID": (
        "Vamos conferir esse CPF?",
        "O número digitado não passou na verificação. Confira dígito por dígito.",
    ),
    "CPF_NOT_FOUND": (
        "CPF não encontrado",
        "Não achamos esse CPF na base da Receita. Confira os números.",
    ),
    "CPF_SERVICE_DOWN": (
        "Consulta indisponível",
        "A consulta de CPF está fora do ar. Tente de novo em instantes.",
    ),
    "CPF_ALREADY_SET": (
        "Conta já tem CPF",
        "Esta conta já tem um CPF confirmado. Fale com o polo que te indicou.",
    ),
    "EMAIL_INVALID": (
        "Ainda falta um detalhe 😊",
        "Esse e-mail não parece completo. Confere pra gente?",
    ),
    "EMAIL_CONFLICT": (
        "Esse e-mail já tem dono",
        "Este e-mail já está em outra conta. Use outro e-mail seu.",
    ),
    "OTP_INVALID": (
        "Código incorreto",
        "Presta atenção no que tá fazendo 👀 — confere o código no seu WhatsApp.",
    ),
    "PIX_INVALID": (
        "Chave não confirmada",
        "A chave precisa estar no SEU CPF. Confira e tente de novo.",
    ),
    "NO_HUB": (
        "Polo indisponível",
        "Nenhum polo disponível pro cadastro agora. Tente mais tarde.",
    ),
    "RATE_LIMITED": (
        "Calma aí 😅",
        "Muitas tentativas em sequência. Espere um pouco e tente de novo.",
    ),
    "DOC_TYPE_LOCKED": (
        "Documento já escolhido",
        "Você já começou com outro documento. Siga com ele.",
    ),
    "JOIN_PROFILE_INCOMPLETE": (
        "Cadastro incompleto",
        "Complete sua identidade pra entrar no programa.",
    ),
}


def _domain_feedback(request, exc: DomainError) -> HttpResponse:
    title, text = _ERROR_TEXT.get(exc.code, ("Não deu certo", exc.detail))
    tone = "danger" if exc.status != 429 else "warn"
    return _feedback(request, tone=tone, title=title, text=text, shake=True)


def step_page(step: str):
    """GET de página de passo: captura ?hub, confere o gate e injeta contexto comum."""

    def deco(fn):
        @functools.wraps(fn)
        def wrapper(request, *a, **kw):
            flow.capture_hub(request)
            current = flow.current_step(request)
            if current != step:
                return redirect(flow.step_url(current))
            return fn(request, *a, **kw)

        return wrapper

    return deco


def htmx_action(fn):
    """POST/poll HTMX: DomainError vira parcial de feedback; WRONG_STATUS corrige a rota."""

    @functools.wraps(fn)
    def wrapper(request, *a, **kw):
        try:
            return fn(request, *a, **kw)
        except DomainError as exc:
            if exc.code in ("WRONG_STATUS", "CANDIDATE_NOT_FOUND", "USER_NOT_FOUND"):
                return _go_current(request)
            return _domain_feedback(request, exc)

    return wrapper


def _uid(request) -> str | None:
    return request.session.get(flow.SESSION_UID)


def _digits(v: str) -> str:
    return "".join(c for c in (v or "") if c.isdigit())


# ── entrada / sessão ─────────────────────────────────────────────────────────


def entry(request):
    flow.capture_hub(request)
    return redirect(flow.step_url(flow.current_step(request)))


def logout(request):
    request.session.flush()
    return redirect(reverse("web:check"))


# ── passo 1: WhatsApp ────────────────────────────────────────────────────────


@step_page("check")
def check_page(request):
    return render(
        request, "web/check.html", {"hub": request.session.get(flow.SESSION_HUB)}
    )


@require_POST
@htmx_action
def check_submit(request):
    phone = _digits(request.POST.get("phone"))
    if len(phone) < 10:
        return _feedback(
            request,
            tone="danger",
            title="Confere esse número?",
            text="Digite o DDD + número do seu WhatsApp.",
            shake=True,
        )

    result = auth_iface.check(phone=phone)
    if result["found"]:
        request.session[flow.SESSION_PENDING] = result["external_id"]
        request.session[flow.SESSION_PHONE] = phone
        return _hx_redirect(reverse("web:otp"))

    if result.get("whatsapp") is False:
        return _feedback(
            request,
            tone="danger",
            shake=True,
            title="Esse número não tem WhatsApp",
            text="O código de acesso chega pelo WhatsApp. Confira o número ou use outro que tenha WhatsApp.",
        )

    # número novo com WhatsApp → cria a conta AQUI (passo 1 é o criador — DOCUMENTACAO,
    # caminho da conta) e o OTP já sai no mesmo passo.
    try:
        reg = auth_iface.register(role="candidate", phone=phone)
    except DomainError as exc:
        if exc.code in ("PHONE_EXISTS", "DUPLICATE"):
            result = auth_iface.check(phone=phone)  # corrida: já existe → login normal
            if result["found"]:
                request.session[flow.SESSION_PENDING] = result["external_id"]
                request.session[flow.SESSION_PHONE] = phone
                return _hx_redirect(reverse("web:otp"))
        raise
    request.session[flow.SESSION_PENDING] = reg["external_id"]
    request.session[flow.SESSION_PHONE] = phone
    return _hx_redirect(reverse("web:otp"))


# ── passo 2: OTP ─────────────────────────────────────────────────────────────


def _masked_phone(request) -> str:
    d = request.session.get(flow.SESSION_PHONE) or ""
    if len(d) >= 10:
        return f"({d[:2]}) •••••-{d[-4:]}"
    return d


@step_page("otp")
def otp_page(request):
    if not request.session.get(flow.SESSION_PENDING):
        return redirect(reverse("web:check"))
    return render(
        request,
        "web/otp.html",
        {"phone_masked": _masked_phone(request), "cooldown": 30},
    )


@require_POST
@htmx_action
def otp_submit(request):
    pending = request.session.get(flow.SESSION_PENDING)
    if not pending:
        return _go_current(request)
    otp = _digits(request.POST.get("otp"))
    if len(otp) != 6:
        return _feedback(
            request,
            tone="danger",
            title="Código incompleto",
            text="Digite os 6 números que chegaram no seu WhatsApp.",
            shake=True,
        )

    try:
        candidate_iface.join_candidate(
            user_external_id=pending,
            otp=otp,
            hub=request.session.get(flow.SESSION_HUB) or None,
        )
    except DomainError as exc:
        if exc.code == "OTP_EXPIRED":
            result = auth_iface.check(
                external_id=pending
            )  # reenvia sozinho (protótipo: ampulheta)
            wait = result.get("otp_wait")
            if result.get("otp_sent"):
                return _feedback(
                    request,
                    tone="warn",
                    shake=True,
                    title="Código expirado — mandei outro",
                    text="Enviamos um novo código no seu WhatsApp. O anterior não vale mais.",
                )
            return _feedback(
                request,
                tone="warn",
                shake=True,
                title="Código expirado",
                text=f"Esse código não vale mais. Peça outro no botão Reenviar{f' em {wait}s' if wait else ''}.",
            )
        if exc.code == "JOIN_PROFILE_INCOMPLETE":
            # conta de OUTRO funil sem identidade completa: prova a posse mesmo assim e deixa o
            # gate levar pro CPF/e-mail; o candidate nasce quando a identidade completar.
            user = User.objects.filter(external_id=pending).first()
            if user is None:
                return _go_current(request)
            auth_iface.verify_otp_for_user(user=user, otp=otp)
        else:
            raise
    request.session[flow.SESSION_UID] = pending
    del request.session[flow.SESSION_PENDING]
    return _go_current(request)


@require_POST
@htmx_action
def otp_resend(request):
    if not request.session.get(flow.SESSION_PENDING):
        return _go_current(request)
    result = flow.resend_otp(request)
    if not result.get("otp_sent") and result.get("otp_wait"):
        return _feedback(
            request,
            tone="warn",
            title="Calma aí 😅",
            text=f"Espere {result['otp_wait']}s pra pedir um novo código.",
        )
    return render(request, "web/partials/otp_resent.html", {"cooldown": 30})


# ── passo 3: CPF (descoberta de identidade → pergaminho) ─────────────────────


@step_page("cpf")
def cpf_page(request):
    return render(request, "web/cpf.html")


@require_POST
@htmx_action
def cpf_submit(request):
    uid = _uid(request)
    if not uid:
        return _go_current(request)
    cpf = _digits(request.POST.get("cpf"))
    try:
        identity = auth_iface.confirm_identity(user_external_id=uid, cpf=cpf)
    except DomainError as exc:
        if exc.code == "CPF_CONFLICT":
            # conta-tentativa foi purgada no service; a sessão morre junto (proteção de identidade).
            request.session.flush()
            return render(request, "web/partials/cpf_conflict.html")
        raise
    age = None
    if identity.get("birth_date"):
        from datetime import date

        b = date.fromisoformat(identity["birth_date"])
        today = date.today()
        age = today.year - b.year - ((today.month, today.day) < (b.month, b.day))
    return render(
        request,
        "web/partials/pergaminho.html",
        {"identity": identity, "age": age, "next_url": reverse("web:email")},
    )


# ── passo 4: e-mail ──────────────────────────────────────────────────────────


@step_page("email")
def email_page(request):
    return render(request, "web/email.html")


@require_POST
@htmx_action
def email_submit(request):
    uid = _uid(request)
    if not uid:
        return _go_current(request)
    result = auth_iface.set_email(
        user_external_id=uid, email=request.POST.get("email", "")
    )
    return render(
        request,
        "web/partials/email_ok.html",
        {
            "already_yours": result.get("already_yours"),
            "next_url": flow.step_url(flow.current_step(request)),
        },
    )


# ── wizard: contexto comum ───────────────────────────────────────────────────


def _me(request) -> dict:
    cand = candidate_iface.get_for_user_external_id(_uid(request))
    return candidate_iface.me_dict(cand)


def _wizard_ctx(request, step: str, **extra) -> dict:
    me = _me(request)
    first_name = ((me.get("profile") or {}).get("name") or "").split(" ")[0].title()
    return {
        "me": me,
        "first_name": first_name,
        "steps": flow.wizard_progress(step),
        **extra,
    }


# ── endereço (CEP → campos → comprovante) ────────────────────────────────────


@step_page("address")
def address_page(request):
    return render(request, "web/address.html", _wizard_ctx(request, "address"))


@require_POST
@htmx_action
def address_cep(request):
    uid = _uid(request)
    me = candidate_iface.set_address_cep(
        user_external_id=uid, cep=_digits(request.POST.get("cep"))
    )
    return render(request, "web/partials/address_form.html", {"me": me})


@require_POST
@htmx_action
def address_data(request):
    uid = _uid(request)
    fields = {
        k: (request.POST.get(k) or "").strip()
        for k in ("street", "number", "complement", "neighborhood", "city", "state")
    }
    me = candidate_iface.set_address_data(user_external_id=uid, **fields)
    complete = not me["address"]["missing_fields"]
    proof_ok = bool((me.get("address_proof") or {}).get("exists"))
    if complete and proof_ok:
        return _hx_redirect(reverse("web:document"))
    return render(request, "web/partials/address_form.html", {"me": me, "saved": True})


@require_POST
@htmx_action
def address_proof(request):
    uid = _uid(request)
    upload = request.FILES.get("file")
    if upload is None:
        return _feedback(
            request,
            tone="danger",
            title="Nenhum arquivo",
            text="Escolha a foto ou o PDF do comprovante.",
            shake=True,
        )
    me = candidate_iface.upload_address_proof(user_external_id=uid, upload=upload)
    complete = not me["address"]["missing_fields"]
    if complete:
        return _hx_redirect(reverse("web:document"))
    return render(request, "web/partials/address_form.html", {"me": me, "saved": True})


# ── documento (foto-primeiro → análise IA → campos que faltam) ───────────────


def _doc_ctx(request) -> dict:
    uid = _uid(request)
    section = candidate_iface.get_document_section(user_external_id=uid)
    me = _me(request)
    analyzing = section.get("analysis_status") in ("pending", "processing") or any(
        (p or {}).get("status") in ("pending", "processing")
        for p in (section.get("photos") or {}).values()
    )
    has_photo = bool(section.get("front_photo") or section.get("full_photo"))
    field_labels = {
        "number": ("Número do documento", "text"),
        "issuing_agency": ("Órgão emissor", "text"),
        "issue_date": ("Data de emissão", "date"),
        "date_of_birth": ("Data de nascimento", "date"),
        "expires_on": ("Validade", "date"),
        "category": ("Categoria", "text"),
        "national_register": ("Nº de registro", "text"),
    }
    missing = [
        {
            "name": f,
            "label": field_labels.get(f, (f, "text"))[0],
            "type": field_labels.get(f, (f, "text"))[1],
        }
        for f in (section.get("missing_fields") or [])
    ]
    photos = section.get("photos") or {}

    def _slot(slot, label, hint):
        p = photos.get(slot) or {}
        return {
            "slot": slot,
            "label": label,
            "hint": hint,
            "status": p.get("status"),
            "reason": p.get("reason"),
            "is_next": section.get("next_slot") == slot,
        }

    rg_slots = [
        _slot("rg_front", "Frente do RG", "O lado com a foto"),
        _slot("rg_back", "Verso do RG", "O lado com os dados"),
    ]
    cnh_slots = [_slot("cnh_full", "CNH aberta", "O documento inteiro, aberto")]
    return {
        "section": section,
        "me": me,
        "analyzing": analyzing,
        "has_photo": has_photo,
        "missing": missing,
        "rg_slots": rg_slots,
        "cnh_slots": cnh_slots,
    }


@step_page("document")
def document_page(request):
    ctx = _wizard_ctx(request, "document")
    ctx.update(_doc_ctx(request))
    return render(request, "web/document.html", ctx)


@require_POST
@htmx_action
def document_photo(request, slot: str):
    uid = _uid(request)
    upload = request.FILES.get("file")
    if upload is None:
        return _feedback(
            request,
            tone="danger",
            title="Nenhuma foto",
            text="Tire a foto ou escolha o arquivo do documento.",
            shake=True,
        )
    candidate_iface.upload_document_photo(
        user_external_id=uid, slot=slot, upload=upload
    )
    return render(request, "web/partials/document_panel.html", _doc_ctx(request))


@require_GET
@htmx_action
def document_status(request):
    ctx = _doc_ctx(request)
    if ctx["me"]["status"] not in ("address", "documents"):
        return _hx_redirect(flow.step_url(flow.current_step(request)))
    return render(request, "web/partials/document_panel.html", ctx)


@require_POST
@htmx_action
def document_fields(request):
    uid = _uid(request)
    fields = {
        k: v.strip()
        for k, v in request.POST.items()
        if k
        in (
            "number",
            "issuing_agency",
            "issue_date",
            "category",
            "national_register",
            "date_of_birth",
            "expires_on",
        )
        and v.strip()
    }
    me = candidate_iface.patch_document_section(user_external_id=uid, **fields)
    if me["status"] not in ("address", "documents"):
        return _hx_redirect(reverse("web:pix"))
    return render(request, "web/partials/document_panel.html", _doc_ctx(request))


# ── pix ──────────────────────────────────────────────────────────────────────


@step_page("pix")
def pix_page(request):
    return render(request, "web/pix.html", _wizard_ctx(request, "pix"))


@require_POST
@htmx_action
def pix_submit(request):
    uid = _uid(request)
    key = (request.POST.get("key") or "").strip()
    key_type = (request.POST.get("key_type") or "").strip()
    if not key or not key_type:
        return _feedback(
            request,
            tone="danger",
            title="Falta a chave",
            text="Digite a sua chave Pix (CPF, celular, e-mail ou aleatória).",
            shake=True,
        )
    candidate_iface.set_pix(user_external_id=uid, key=key, key_type=key_type)
    return render(
        request, "web/partials/pix_ok.html", {"next_url": reverse("web:education")}
    )


# ── escolaridade ─────────────────────────────────────────────────────────────


@step_page("education")
def education_page(request):
    return render(request, "web/education.html", _wizard_ctx(request, "education"))


@require_POST
@htmx_action
def education_submit(request):
    uid = _uid(request)
    level = request.POST.get("level") or ""
    situacao = request.POST.get("situacao") or ""  # completed | attending | stopped
    grade_raw = _digits(request.POST.get("grade"))
    year_raw = _digits(request.POST.get("year"))
    school = (request.POST.get("school") or "").strip() or None
    city = (request.POST.get("city") or "").strip() or None
    if level not in ("fundamental", "medio", "superior") or situacao not in (
        "completed",
        "attending",
        "stopped",
    ):
        return _feedback(
            request,
            tone="danger",
            title="Faltou escolher",
            text="Marque o nível e a situação dos seus estudos.",
            shake=True,
        )
    completed = situacao == "completed"
    grade = int(grade_raw) if grade_raw and level != "superior" else None
    if completed and level in ("fundamental", "medio"):
        grade = 9 if level == "fundamental" else 3
    candidate_iface.set_education(
        user_external_id=uid,
        level=level,
        completed=completed,
        grade=grade,
        education_status=situacao,
        year=int(year_raw) if year_raw else None,
        school=school,
        city=city,
    )
    return _hx_redirect(reverse("web:selfie"))


# ── selfie (contrato + câmera + análise) ─────────────────────────────────────


@step_page("selfie")
def selfie_page(request):
    ctx = _wizard_ctx(request, "selfie", contract=PROMOTER_CONTRACT.as_dict())
    ctx["selfie"] = ctx["me"].get("selfie") or {}
    return render(request, "web/selfie.html", ctx)


@require_POST
@htmx_action
def selfie_submit(request):
    uid = _uid(request)
    upload = request.FILES.get("file")
    if upload is None:
        return _feedback(
            request,
            tone="danger",
            title="Cadê a selfie?",
            text="Tire a foto com a câmera da frente, num lugar iluminado.",
            shake=True,
        )
    candidate_iface.set_selfie(
        user_external_id=uid,
        image_bytes=upload.read(),
        content_type=upload.content_type or "image/jpeg",
        consent_ip=request.META.get("REMOTE_ADDR"),
        consent_user_agent=request.headers.get("User-Agent"),
    )
    return render(
        request,
        "web/partials/selfie_status.html",
        {"selfie": {"analysis_status": "pending"}},
    )


@require_GET
@htmx_action
def selfie_status(request):
    uid = _uid(request)
    selfie = candidate_iface.get_selfie(user_external_id=uid)
    status = selfie.get("analysis_status") or selfie.get("status")
    if status == "approved":
        return _hx_redirect(reverse("web:analysis"))
    return render(request, "web/partials/selfie_status.html", {"selfie": selfie})


# ── análise final (checks + blocks) ──────────────────────────────────────────


def _analysis_ctx(request) -> dict:
    me = _me(request)
    section = candidate_iface.get_document_section(user_external_id=_uid(request))
    return {
        "me": me,
        "doc_status": section.get("validation_status")
        or section.get("analysis_status"),
        "proof": me.get("address_proof") or {},
        "selfie": me.get("selfie") or {},
        "blocks": me.get("blocks") or [],
    }


@step_page("analysis")
def analysis_page(request):
    ctx = _wizard_ctx(request, "done")
    ctx.update(_analysis_ctx(request))
    return render(request, "web/analysis.html", ctx)


@require_GET
@htmx_action
def analysis_status(request):
    user = flow.user_for(request)
    if user is None:
        return _go_current(request)
    active = roles.active_roles(user)
    if "promoter" in active or "coordinator" in active:
        return _hx_redirect(flow.step_url(flow.current_step(request)))
    return render(request, "web/partials/analysis_grid.html", _analysis_ctx(request))


# ── treino (LMS v1: texto) ───────────────────────────────────────────────────


def _training_ctx(request) -> dict:
    uid = _uid(request)
    materials = training_iface.assigned_materials(uid)
    active = next(
        (m for m in materials if m.get("submission_status") not in ("approved",)), None
    )
    return {"materials": materials, "active": active}


@step_page("training")
def training_page(request):
    return render(request, "web/training.html", _training_ctx(request))


@require_POST
@htmx_action
def training_submit(request):
    uid = _uid(request)
    answer = (request.POST.get("answer") or "").strip()
    material = request.POST.get("material") or ""
    if len(answer) < 10:
        return _feedback(
            request,
            tone="danger",
            title="Resposta curta demais",
            text="Explica com suas palavras — pelo menos uma frase inteira.",
            shake=True,
        )
    sub = training_iface.submit(
        user_external_id=uid, material_external_id=material, answer=answer
    )
    training_iface.submission_to_dict(sub)
    return render(request, "web/partials/training_wait.html", {})


@require_GET
@htmx_action
def training_status(request):
    user = flow.user_for(request)
    if user is None:
        return _go_current(request)
    if not training_iface.is_locked(user):
        return _hx_redirect(reverse("web:panel"))
    ctx = _training_ctx(request)
    active = ctx.get("active") or {}
    if active.get("submission_status") in ("grading", "pending", "submitted"):
        return render(request, "web/partials/training_wait.html", {})
    return _hx_redirect(reverse("web:training"))


# ── painel do promotor (v1) ──────────────────────────────────────────────────


@step_page("panel")
def panel_page(request):
    uid = _uid(request)
    promoter = promoter_iface.get_by_user_external_id(uid)
    if promoter is None:
        return redirect(flow.step_url(flow.current_step(request)))
    info = promoter_iface.to_dict(promoter)
    summary = promoter_iface.summary(promoter.user)
    leads = promoter_iface.list_leads(promoter.user)
    profile = profiles.get(promoter.user)
    first_name = ((profile.name if profile else "") or "Promotor").split(" ")[0].title()
    import urllib.parse

    share_text = urllib.parse.quote(
        "Terminar os estudos mudou tudo pra mim. Faz sua matrícula aqui: "
        + info["ref_url"]
    )
    return render(
        request,
        "web/panel.html",
        {
            "info": info,
            "summary": summary,
            "leads": leads,
            "first_name": first_name,
            "share_text": share_text,
            "goal_stars": [
                i < summary["week_paid_leads"] for i in range(summary["week_goal"])
            ],
            "missing_for_goal": max(
                0, summary["week_goal"] - summary["week_paid_leads"]
            ),
            "week_total": _money(summary["week_commission_total"]),
            "bonus": _money(summary["bonus_amount"]),
            "lifetime_total": _money(summary["lifetime"]["total_received"]),
            "closing_label": _closing_label(summary["next_closing_at"]),
        },
    )


@require_POST
@htmx_action
def panel_invite(request):
    uid = _uid(request)
    promoter = promoter_iface.get_by_user_external_id(uid)
    if promoter is None:
        return _go_current(request)
    result = promoter_iface.invite_lead(
        promoter=promoter,
        phone=_digits(request.POST.get("phone")),
        cpf=_digits(request.POST.get("cpf")),
    )
    return render(request, "web/partials/invite_ok.html", {"result": result})


_MESES = (
    "janeiro",
    "fevereiro",
    "março",
    "abril",
    "maio",
    "junho",
    "julho",
    "agosto",
    "setembro",
    "outubro",
    "novembro",
    "dezembro",
)
_DIAS = ("segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo")


def _money(value) -> str:
    """'1234.5' → 'R$ 1.234,50' (regra A6: sempre pt-BR com milhar)."""
    try:
        n = float(value)
    except (TypeError, ValueError):
        return "R$ 0,00"
    inteiro, cents = divmod(round(n * 100), 100)
    return "R$ " + f"{inteiro:,}".replace(",", ".") + f",{cents:02d}"


def _closing_label(iso: str) -> str:
    """ISO → 'sexta, 31 de julho · 18h' (data REAL do fechamento, nunca 'toda sexta')."""
    from datetime import datetime

    try:
        dt = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return ""
    return f"{_DIAS[dt.weekday()]}, {dt.day} de {_MESES[dt.month - 1]} · {dt.hour}h"
