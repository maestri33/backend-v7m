"""Views do funil web — casca fina (HTMX) sobre os services de `users/` (in-process).

Padrão: cada PASSO tem página própria (GET, guardada pelo gate) + endpoints HTMX (POST/poll)
que devolvem parciais. Sucesso que muda de passo → `HX-Redirect` (página cheia, URL certa).
`DomainError` → parcial de feedback com a mensagem; `WRONG_STATUS` → corrige a rota (gate)."""

from __future__ import annotations

import functools

from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from urllib.parse import quote

import structlog
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from finance import config as money_config
from users.auth import service as auth_iface
from users.auth.models import User
from users.consent import PROMOTER_CONTRACT
from users.exceptions import DomainError
from users.profiles import interface as profiles
from users.roles import interface as roles
from users.roles.candidate import service as candidate_iface
from users.roles.promoter import service as promoter_iface
from users.roles.training import service as training_iface
from web import assistant, flow
from web import panel_data

# ── helpers ──────────────────────────────────────────────────────────────────


logger = structlog.get_logger(__name__)


def _hx_redirect(url: str) -> HttpResponse:
    resp = HttpResponse(status=204)
    resp["HX-Redirect"] = url
    return resp


def _go_current(request) -> HttpResponse:
    """Manda o cliente pra rota do passo REAL (gate A3)."""
    url = flow.step_url(flow.current_step(request), request)
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
    # ── endereço ─────────────────────────────────────────────────────────────
    "CEP_NOT_FOUND": (
        "CEP não encontrado",
        "Não achamos esse CEP. Confira os 8 números.",
    ),
    "CEP_INVALID": ("CEP inválido", "O CEP tem 8 números. Confira e digite de novo."),
    "CEP_SERVICE_DOWN": (
        "Busca de CEP fora do ar",
        "A consulta de endereço está indisponível agora — não é você. Tente em instantes.",
    ),
    "ADDRESS_NOT_FOUND": (
        "Endereço não encontrado",
        "Comece pelo CEP para a gente montar seu endereço.",
    ),
    # ── documento ────────────────────────────────────────────────────────────
    "DOCUMENT_SIDE_DUPLICATE": (
        "Essa foto é igual à outra",
        "A frente e o verso precisam ser fotos diferentes do documento.",
    ),
    "IMAGE_TYPE_INVALID": (
        "Arquivo não aceito",
        "Envie uma foto (JPG ou PNG) ou um PDF do documento.",
    ),
    "IMAGE_DECODE_FAILED": (
        "Não conseguimos abrir o arquivo",
        "A imagem parece corrompida. Tire outra foto e envie de novo.",
    ),
    "IMAGE_TOO_LARGE": (
        "Arquivo muito grande",
        "A foto passou do tamanho permitido. Tente uma imagem menor.",
    ),
    "DOC_TYPE_NOT_SET": (
        "Escolha o documento",
        "Selecione RG ou CNH antes de enviar a foto.",
    ),
    "SLOT_INVALID": (
        "Envio inválido",
        "Recarregue a página e tente enviar a foto de novo.",
    ),
    "INVALID_DOC_TYPE": ("Documento não aceito", "Use RG ou CNH."),
    "DOCUMENT_NOT_FOUND": (
        "Documento não encontrado",
        "Envie a foto do documento para continuar.",
    ),
    # ── escolaridade ─────────────────────────────────────────────────────────
    "EDUCATION_LEVEL_INVALID": (
        "Nível inválido",
        "Escolha entre Fundamental, Médio ou Superior.",
    ),
    "EDUCATION_GRADE_INVALID": (
        "Série incompatível",
        "A série não bate com o nível escolhido. Confira.",
    ),
    "EDUCATION_STATUS_INVALID": (
        "Situação inválida",
        "Diga se concluiu, está cursando ou parou no meio.",
    ),
    "EDUCATION_YEAR_INVALID": (
        "Ano inválido",
        "Confira o ano em que você estudou pela última vez.",
    ),
    "EDUCATION_LAST_COMPLETED_GRADE_INVALID": (
        "Séries não batem",
        "A última série concluída precisa ser anterior à que você cursava.",
    ),
    # ── pix / perfil ─────────────────────────────────────────────────────────
    "PROFILE_CPF_MISSING": (
        "Falta o seu CPF",
        "Confirme sua identidade antes de cadastrar a chave Pix.",
    ),
    "DATE_INVALID": ("Data inválida", "Confira a data digitada (dia/mês/ano)."),
    "STATE_INVALID": (
        "UF inválida",
        "Use a sigla do estado com 2 letras (ex.: PR, SP).",
    ),
}


# Erros que o protótipo mostra como MODAL (prints NN-modais) — são os que BLOQUEIAM o passo:
# não adianta continuar digitando, a pessoa precisa ler e decidir. O resto segue como alerta
# inline dentro do card. `icon`/`support` espelham cada print.
_ERROR_MODAL = {
    "OTP_INVALID": {
        "title": "Código incorreto",
        "text": "O código não confere. Dê uma olhada no WhatsApp e digite novamente.",
        "action_label": "Digitar de novo",
        "clear_code": True,
    },
    "OTP_EXPIRED": {
        "title": "Código expirado",
        "text": "Esse código venceu. Peça um novo no botão Reenviar e confira o WhatsApp.",
        "icon": "clock",
        "action_label": "Pedir outro código",
        "clear_code": True,
    },
    "CPF_CONFLICT": {
        "title": "CPF já vinculado",
        "text": (
            "Encontramos uma conta com este CPF vinculada a outro número. Se você trocou "
            "de número, fale com seu polo para recuperar o acesso."
        ),
        "icon": "shield",
        "action_url": "/app/verificar",
        "support": True,
    },
    "CPF_INVALID": {
        "title": "Vamos conferir esse CPF?",
        "text": "O número digitado não passou na verificação. Confira dígito por dígito.",
        "action_label": "Revisar CPF",
        "clear_code": True,
    },
    "CPF_NOT_FOUND": {
        "title": "CPF não encontrado",
        "text": "Não achamos esse CPF na base da Receita. Confira os números e tente de novo.",
        "action_label": "Revisar CPF",
        "clear_code": True,
    },
    "EMAIL_CONFLICT": {
        "title": "Esse e-mail já tem dono",
        "text": "Este e-mail já está em outra conta. Use outro e-mail seu para seguir.",
        "icon": "shield",
        "action_label": "Trocar e-mail",
        "support": True,
    },
    "CPF_SERVICE_DOWN": {
        "title": "Instabilidade no servidor",
        "text": "A consulta está fora do ar por instantes — não é você. Tente em alguns segundos.",
        "action_label": "Tentar de novo",
    },
    "RATE_LIMITED": {
        "title": "Calma aí",
        "text": "Foram muitas tentativas seguidas. Espere um pouquinho antes de pedir outro código.",
        "icon": "clock",
    },
}


def _modal(request, **ctx) -> HttpResponse:
    return render(request, "web/partials/modal.html", ctx)


def _domain_feedback(request, exc: DomainError) -> HttpResponse:
    spec = _ERROR_MODAL.get(exc.code)
    if spec is not None:
        spec = dict(spec)
        clear = spec.pop("clear_code", False)
        resp = _modal(request, tone="danger", **spec)
        if clear:
            # marcador lido no htmx:afterSwap para limpar as caixas de código
            resp.write('<span class="js-clear-code" hidden></span>')
        return resp
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
                return redirect(flow.step_url(current, request))
            return fn(request, *a, **kw)

        return wrapper

    return deco


def panel_route(fn):
    """Guard das telas do painel: o gate é o mesmo (`current_step` tem de ser `panel`),
    mas a sub-rota escolhida não é forçada — o promotor navega livre pela navbar."""

    @functools.wraps(fn)
    def wrapper(request, *a, **kw):
        flow.capture_hub(request)
        current = flow.current_step(request)
        if current != "panel":
            return redirect(flow.step_url(current, request))
        promoter = promoter_iface.get_by_user_external_id(_uid(request))
        if promoter is None:
            # role de promotor sem registro Promoter: redirecionar pro painel aqui vira LOOP
            # (o gate manda pro painel, o painel manda pro gate). Sai do laço com a verdade.
            logger.error("web.panel.sem_promoter", uid=_uid(request))
            return render(
                request,
                "web/erro.html",
                {
                    "titulo": "Seu acesso de promotor ainda não abriu",
                    "texto": (
                        "Sua conta está aprovada, mas o cadastro de promotor não foi criado. "
                        "Fala com o seu polo — eles destravam isso em minutos."
                    ),
                },
                status=409,
            )
        return fn(request, promoter, *a, **kw)

    return wrapper


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


# DDDs que existem no Brasil (Anatel). O `validate_phone` do backend só confere o TAMANHO
# (10/11 dígitos), então "00000000000" e "4200000000" passavam e viravam conta de verdade —
# bug achado no E2E de produção 2026-07-28. A borda do funil valida de fato antes de criar.
_DDDS = {
    11,
    12,
    13,
    14,
    15,
    16,
    17,
    18,
    19,
    21,
    22,
    24,
    27,
    28,
    31,
    32,
    33,
    34,
    35,
    37,
    38,
    41,
    42,
    43,
    44,
    45,
    46,
    47,
    48,
    49,
    51,
    53,
    54,
    55,
    61,
    62,
    63,
    64,
    65,
    66,
    67,
    68,
    69,
    71,
    73,
    74,
    75,
    77,
    79,
    81,
    82,
    83,
    84,
    85,
    86,
    87,
    88,
    89,
    91,
    92,
    93,
    94,
    95,
    96,
    97,
    98,
    99,
}


def _phone_problem(digits: str) -> str | None:
    """Motivo pelo qual o número NÃO serve, ou None se está ok.

    WhatsApp no Brasil é celular: 11 dígitos, DDD válido, nono dígito 9. Fixo (10 dígitos)
    é recusado aqui de propósito — o funil inteiro depende de receber OTP no WhatsApp."""
    if len(digits) < 10:
        return "Faltam números. Digite o DDD + o número do seu WhatsApp."
    if len(digits) > 11:
        return "Esse número tem dígitos demais. Confira o DDD + o número."
    if int(digits[:2]) not in _DDDS:
        return f"O DDD {digits[:2]} não existe. Confira os dois primeiros números."
    if len(digits) == 10 or digits[2] != "9":
        return "Precisa ser um celular com WhatsApp (11 dígitos, começando com 9 depois do DDD)."
    return None


# ── entrada / sessão ─────────────────────────────────────────────────────────


def entry(request):
    flow.capture_hub(request)
    return redirect(flow.step_url(flow.current_step(request), request))


def csrf_failure(request, reason=""):
    """CSRF inválido/ausente numa rota do funil → parcial amigável no lugar da página 403 crua.

    Acontece de verdade quando a aba fica aberta tempo demais e o cookie de sessão expira: o
    HTMX injetava a página 403 INTEIRA do Django dentro do card (achado no E2E de produção
    2026-07-28). Fora de /app/ o Django segue com a página padrão."""
    if not request.path.startswith("/app/"):
        from django.views.csrf import csrf_failure as django_csrf_failure

        return django_csrf_failure(request, reason=reason)
    resp = _feedback(
        request,
        tone="warn",
        shake=True,
        title="Sua sessão expirou",
        text="Recarregue a página e tente de novo — leva um segundo.",
    )
    resp["HX-Trigger"] = "csrf-expired"
    return resp


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
    problem = _phone_problem(phone)
    if problem:
        return _feedback(
            request,
            tone="danger",
            title="Confere esse número?",
            text=problem,
            shake=True,
        )

    result = auth_iface.check(phone=phone)
    if result["found"]:
        request.session[flow.SESSION_PENDING] = result["external_id"]
        request.session[flow.SESSION_PHONE] = phone
        return _hx_redirect(reverse("web:otp"))

    if result.get("whatsapp") is None:
        # WhatsApp indeterminado (Evolution fora do ar / erro): NÃO cria conta. Antes o funil
        # seguia pro register, que também trata a falha como "existe" — resultado: conta real
        # criada a partir de um número que ninguém confirmou. Protótipo prevê este estado
        # ("Não conseguimos confirmar"). Bug achado no E2E de produção 2026-07-28.
        return _modal(
            request,
            tone="danger",
            icon="wifi",
            title="Não conseguimos confirmar",
            text="A checagem do WhatsApp falhou do nosso lado — não é você. Tente de novo em instantes.",
            action_label="Tentar de novo",
        )

    if result.get("whatsapp") is False:
        return _modal(
            request,
            tone="danger",
            icon="phone",
            title="Número sem WhatsApp",
            text="O código de acesso chega pelo WhatsApp. Confira o número ou use outro que tenha WhatsApp ativo.",
            action_label="Corrigir número",
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
        {
            "identity": identity,
            "age": age,
            # o protótipo flexiona o texto do pergaminho pelo gênero da identidade
            # ("Bem-vinda ao time · Sua jornada como promotora começa agora")
            # dois sufixos: "Bem-vind{o|a}" leva vogal sempre; "promotor{|a}" só no feminino
            "suffix": "a" if (identity.get("sex") or "").upper() == "F" else "o",
            "suffix_f": "a" if (identity.get("sex") or "").upper() == "F" else "",
            "first_name": (identity.get("name") or "").split(" ")[0].title(),
            "next_url": reverse("web:email"),
        },
    )


# ── comprovante no nome de outra pessoa: quem é? ─────────────────────────────

# Sugestões prontas (protótipo: a pessoa toca, não redige). "Outro" abre o campo livre.
_PARENTESCO = (
    "Pai ou mãe",
    "Cônjuge ou companheiro(a)",
    "Filho(a)",
    "Irmão ou irmã",
    "Avô ou avó",
    "Sogro(a)",
    "Moro de aluguel",
)


@step_page("kinship")
def kinship_page(request):
    from users.roles import _address_proof

    sec = _address_proof.section_dict(_uid(request))
    return render(
        request,
        "web/kinship.html",
        {
            "kinship_kind": sec.get("kinship_kind"),
            "holder": sec.get("holder_name"),
            "opcoes": _PARENTESCO,
        },
    )


@require_POST
@htmx_action
def kinship_submit(request):
    relation = (request.POST.get("relation") or "").strip()
    if not relation:
        return _feedback(
            request,
            tone="danger",
            title="Falta contar quem é",
            text="Escolha uma opção ou escreva com as suas palavras.",
            shake=True,
        )
    candidate_iface.submit_address_proof_kinship(
        user_external_id=_uid(request), relation=relation
    )
    from users.roles import _address_proof

    sec = _address_proof.section_dict(_uid(request))
    if sec.get("needs_kinship"):
        # a IA não achou fundamento na explicação — a pessoa reescreve (human-in-the-loop)
        return _feedback(
            request,
            tone="danger",
            title="Não deu pra entender",
            text="Explica de novo, com um pouco mais de detalhe: quem é o titular e por que você mora aí.",
            shake=True,
        )
    return _hx_redirect(flow.step_url(flow.current_step(request), request))


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
            "next_url": flow.step_url(flow.current_step(request), request),
        },
    )


# ── wizard: contexto comum ───────────────────────────────────────────────────


def _me(request) -> dict:
    cand = candidate_iface.get_for_user_external_id(_uid(request))
    return candidate_iface.me_dict(cand)


# eyebrow de cada etapa (protótipo: rótulo próprio por etapa, não "cadastro" genérico)
_STEP_EYEBROW = {
    "address": "Comprovante",
    "document": "Identificação",
    "pix": "Recebimento",
    "education": "Formação",
    "selfie": "Confirmação",
}


def _wizard_ctx(request, step: str, **extra) -> dict:
    me = _me(request)
    first_name = ((me.get("profile") or {}).get("name") or "").split(" ")[0].title()
    return {
        "me": me,
        "first_name": first_name,
        "steps": flow.wizard_progress(step),
        "eyebrow": _STEP_EYEBROW.get(step, "Cadastro do promotor"),
        **extra,
    }


# ── endereço (CEP → campos → comprovante) ────────────────────────────────────


@step_page("address")
def address_page(request):
    """Passo do endereço = SÓ o comprovante (protótipo). Nada de CEP: a IA lê o documento e
    preenche logradouro/número/bairro/cidade/UF — o backend já faz isso em
    `_address_proof.validate_and_store`, que chama `address_iface.fill_empty` com o extraído."""
    return render(request, "web/address.html", _wizard_ctx(request, "address"))


@require_GET
@htmx_action
def address_status(request):
    """Poll enquanto a IA lê o comprovante. Assim que o endereço estiver completo, avança."""
    me = _me(request)
    if me["status"] not in ("profile", "address"):
        return _hx_redirect(flow.step_url(flow.current_step(request), request))
    return render(request, "web/partials/address_form.html", {"me": me})


@require_POST
@htmx_action
def address_data(request):
    """Completa APENAS o que a leitura do comprovante não trouxe. Não é formulário de endereço:
    o CEP e o resto vêm da própria conta enviada (o promotor não digita duas vezes)."""
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
    return render(request, "web/partials/address_form.html", {"me": me})


@require_POST
@htmx_action
def address_proof(request):
    """Sobe o comprovante e devolve na hora — a leitura corre assíncrona no worker e a tela
    acompanha por poll. O promotor não espera parado nem digita endereço."""
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
    if not me["address"]["missing_fields"] and me["status"] not in (
        "profile",
        "address",
    ):
        return _hx_redirect(reverse("web:document"))
    return render(request, "web/partials/address_form.html", {"me": me})


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

    def _slot(slot, label, hint, *, accept="image/*", capture=""):
        """`accept`/`capture` mudam por método (Victor 2026-07-29): foto abre a câmera; arquivo
        abre o seletor. Sem separar os dois, o `capture` do celular NUNCA oferece o arquivo —
        quem tem a CNH digital em PDF ficava sem caminho."""
        p = photos.get(slot) or {}
        return {
            "slot": slot,
            "label": label,
            "hint": hint,
            "accept": accept,
            "capture": capture,
            "status": p.get("status"),
            "reason": p.get("reason"),
            "is_next": section.get("next_slot") == slot,
        }

    IMG = "image/*"
    ARQ = "image/*,application/pdf"
    rg_foto = [
        _slot(
            "rg_front",
            "Frente do RG",
            "O lado com a foto",
            accept=IMG,
            capture="environment",
        ),
        _slot(
            "rg_back",
            "Verso do RG",
            "O lado com os dados",
            accept=IMG,
            capture="environment",
        ),
    ]
    rg_arquivo = [
        _slot(
            "rg_front", "Frente do RG", "Imagem ou PDF do lado com a foto", accept=ARQ
        ),
        _slot(
            "rg_back", "Verso do RG", "Imagem ou PDF do lado com os dados", accept=ARQ
        ),
    ]
    # CNH: o PDF do gov.br vale sozinho (traz frente e verso); por foto, precisa dos dois lados.
    cnh_pdf = [
        _slot(
            "cnh_full",
            "CNH Digital (PDF)",
            "O arquivo baixado do gov.br / CDT",
            accept="application/pdf",
        )
    ]
    cnh_foto = [
        _slot(
            "cnh_front",
            "Frente da CNH",
            "O lado com a foto",
            accept=IMG,
            capture="environment",
        ),
        _slot(
            "cnh_back",
            "Verso da CNH",
            "O lado com os dados",
            accept=IMG,
            capture="environment",
        ),
    ]
    return {
        "section": section,
        "me": me,
        "analyzing": analyzing,
        "has_photo": has_photo,
        "missing": missing,
        "rg_foto": rg_foto,
        "rg_arquivo": rg_arquivo,
        "cnh_pdf": cnh_pdf,
        "cnh_foto": cnh_foto,
        # o que já foi enviado manda na tela de status/reenvio
        "rg_slots": rg_foto,
        "cnh_slots": cnh_pdf
        if (photos.get("cnh_full") or {}).get("status")
        else cnh_foto,
    }


@step_page("document")
def document_page(request):
    ctx = _wizard_ctx(request, "document")
    ctx.update(_doc_ctx(request))
    return render(request, "web/document.html", ctx)


@require_POST
@htmx_action
def document_classify(request):
    """IA de leve, NA HORA: a foto passa por uma checagem rápida antes de subir de verdade.

    É o contrato do protótipo ("a leitura é automática: você só tira a foto"): em vez de mandar
    e esperar o ciclo assíncrono para descobrir que ficou tremida, a pessoa sabe em ~1s se serve.
    A validação minuciosa (OCR, extração, biometria) segue assíncrona, sem travar ninguém."""
    upload = request.FILES.get("file")
    if upload is None:
        return HttpResponse(status=204)
    _guard_candidate(request)
    from integrations.ai import service as ai

    data = upload.read()
    try:
        r = ai.classify_document(
            data,
            caller="web.candidate.classify",
            mime_type=upload.content_type or "image/jpeg",
        )
    except Exception:  # noqa: BLE001 — IA fora do ar não pode travar o envio
        r = {}
    ok = r.get("is_document") is not False and r.get("is_legible") is not False
    return render(
        request,
        "web/partials/_doc_check.html",
        {
            "ok": ok,
            "doc_type": (r.get("doc_type") or "").upper() or None,
            "completeness": r.get("completeness"),
            "reason": r.get("reason"),
            "slot": request.POST.get("slot") or "",
        },
    )


def _guard_candidate(request):
    """Confere que há candidato na sessão (as views de upload já fazem isso via service)."""
    uid = _uid(request)
    if not uid:
        raise DomainError("Sessão expirada.", code="USER_NOT_FOUND")
    return uid


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
        return _hx_redirect(flow.step_url(flow.current_step(request), request))
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


@require_GET
def cidades(request):
    """Autocomplete de município (IBGE). A UF vem junto — nunca é perguntada."""
    from integrations.tools.ibge import service as ibge

    return JsonResponse(ibge.buscar(request.GET.get("q", "")), safe=False)


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
    uf = (request.POST.get("uf") or "").strip().upper() or None
    if city:
        # a cidade tem de existir no IBGE — o autocomplete já garante, mas ninguém confia no
        # cliente. A UF vem da própria base (nunca é perguntada ao promotor).
        from integrations.tools.ibge import service as ibge

        achado = ibge.existe(city, uf)
        if achado:
            city, uf = achado["nome"], achado["uf"]
        city = f"{city}/{uf}" if uf else city
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
        return _hx_redirect(flow.step_url(flow.current_step(request), request))
    return render(request, "web/partials/analysis_grid.html", _analysis_ctx(request))


# ── treino (LMS v1: texto) ───────────────────────────────────────────────────


def _training_ctx(request) -> dict:
    uid = _uid(request)
    materials = training_iface.assigned_materials(uid)
    # "em correção" (pending) não é aula aberta: quem respondeu já pode seguir.
    abertas = [
        m
        for m in materials
        if m.get("submission_status") not in ("approved", "pending")
    ]
    active = abertas[0] if abertas else None
    return {
        "materials": materials,
        "active": active,
        "open_count": len(abertas),
        # aula sem vídeo publicado ainda precisa do gate, mas reprovada volta destravada
        "watched_default": bool(
            active and active.get("submission_status") == "rejected"
        ),
    }


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
    try:
        training_iface.submit(
            user_external_id=uid, material_external_id=material, answer=answer
        )
    except Exception as exc:  # noqa: BLE001
        return _erro_treino(request, exc)
    # manda e segue: a IA corrige em segundo plano. Se sobrou aula, cai na próxima.
    return _apos_resposta(request, uid)


def _apos_resposta(request, uid: str) -> HttpResponse:
    """Depois de responder, o promotor não espera nota: vai pra próxima aula ou pro painel."""
    restantes = [
        m
        for m in training_iface.assigned_materials(uid)
        if m.get("submission_status") not in ("approved", "pending")
    ]
    destino = reverse("web:training") if restantes else reverse("web:panel")
    return _hx_redirect(destino)


def _erro_treino(request, exc: Exception) -> HttpResponse:
    code = getattr(exc, "code", "") or ""
    return _feedback(
        request,
        tone="danger",
        title="Não deu pra enviar",
        text=_ERROR_TEXT.get(code, str(exc) or "Tenta de novo em instantes."),
        shake=True,
    )


@require_POST
@htmx_action
def training_audio(request):
    """Resposta falada: o backend transcreve e corrige — o promotor só grava e envia."""
    uid = _uid(request)
    up = request.FILES.get("audio")
    material = request.POST.get("material") or ""
    if up is None:
        return _feedback(
            request,
            tone="danger",
            title="Áudio não chegou",
            text="Grava de novo, por favor.",
            shake=True,
        )
    try:
        training_iface.submit_audio(
            user_external_id=uid,
            material_external_id=material,
            data=up.read(),
            content_type=up.content_type or "audio/webm",
        )
    except Exception as exc:  # noqa: BLE001
        return _erro_treino(request, exc)
    return _apos_resposta(request, uid)


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


def _panel_ctx(request, promoter, active: str, **extra) -> dict:
    """Contexto que TODA tela do painel compartilha: header + navbar."""
    profile = profiles.get(promoter.user)
    name = (profile.name if profile else "") or "Promotor"
    return {
        "panel": True,
        "nav": active,
        "full_name": name,
        "first_name": name.split(" ")[0].title(),
        "initials": panel_data.initials(name),
        "hub_label": _hub_label(promoter),
        "info": promoter_iface.to_dict(promoter),
        **extra,
    }


@panel_route
def panel_page(request, promoter):
    """Início: hero do próximo depósito, meta da semana e kanban de leads."""
    summary = promoter_iface.summary(promoter.user)
    leads = panel_data.leads(promoter.user)
    hist = panel_data.cycles(promoter.user)
    return render(
        request,
        "web/panel/home.html",
        _panel_ctx(
            request,
            promoter,
            "home",
            summary=summary,
            leads=leads,
            week_total=_money(summary["week_commission_total"]),
            bonus=_money(summary["bonus_amount"]),
            lifetime_total=hist["total"],
            commission=_money(money_config.direct_amount()),
            closing_label=_closing_label(summary["next_closing_at"]),
            closing_dt=_closing_parts(summary["next_closing_at"]),
            cycle_range=f"{_short_date(summary['week_start'])} – {_short_date(summary['week_end'])}",
            cycle_state=panel_data.cycle_state(promoter.user),
            goal_stars=[
                i < summary["week_paid_leads"] for i in range(summary["week_goal"])
            ],
            missing_for_goal=max(0, summary["week_goal"] - summary["week_paid_leads"]),
        ),
    )


@panel_route
def panel_finance(request, promoter):
    """Finanças: chave Pix verificada + histórico de ciclos."""
    hist = panel_data.cycles(promoter.user)
    return render(
        request,
        "web/panel/finance.html",
        _panel_ctx(
            request,
            promoter,
            "finance",
            pix=panel_data.pix_account(promoter.user),
            cycles=hist["cycles"],
            total=hist["total"],
            record_id=hist["record_id"],
        ),
    )


@panel_route
def panel_cycle(request, promoter, cycle_id: str):
    """Modal de detalhe do ciclo (zerado · processando · pago)."""
    hist = panel_data.cycles(promoter.user)
    cycle = next((c for c in hist["cycles"] if c["id"] == cycle_id), None)
    if cycle is None:
        return HttpResponse(status=204)
    return render(
        request,
        "web/panel/_cycle_modal.html",
        {"cycle": cycle, "is_record": cycle_id == hist["record_id"]},
    )


@panel_route
def panel_referrals(request, promoter):
    """Indicações: todos os leads com status e valor."""
    return render(
        request,
        "web/panel/referrals.html",
        _panel_ctx(
            request,
            promoter,
            "referrals",
            leads=panel_data.leads(promoter.user),
            commission=_money(money_config.direct_amount()),
        ),
    )


@panel_route
def panel_enroll(request, promoter):
    """Matricular: abas Convidar (link + canais) e Iniciar matrícula (só telefone)."""
    import urllib.parse

    info = promoter_iface.to_dict(promoter)
    ref = info["ref_url"]
    text = urllib.parse.quote(
        f"Terminar os estudos mudou tudo pra mim. Faz sua matrícula aqui: {ref}"
    )
    return render(
        request,
        "web/panel/enroll.html",
        _panel_ctx(
            request,
            promoter,
            "enroll",
            ref_url=ref,
            ref_display=ref.replace("https://", "").replace("http://", ""),
            share_text=text,
            share_url=urllib.parse.quote(ref, safe=""),
            commission=_money(money_config.direct_amount()),
        ),
    )


@panel_route
def panel_chat(request, promoter):
    """Bate-papo: assistente que responde com os dados reais do promotor."""
    return render(
        request,
        "web/panel/chat.html",
        _panel_ctx(request, promoter, "chat", chips=assistant.SUGGESTIONS),
    )


@require_POST
@panel_route
def panel_chat_send(request, promoter):
    question = (request.POST.get("q") or "").strip()
    answer = assistant.answer(promoter, question)
    return render(
        request,
        "web/panel/_chat_turn.html",
        {"question": question, "answer": answer},
    )


@panel_route
def panel_personal_data(request, promoter):
    """Dados pessoais: bento + um card por arquivo (somente leitura)."""
    profile = profiles.get(promoter.user)
    from users.address import interface as address_iface

    addr = address_iface.as_public_dict(
        address_iface.get_by_external_id(str(promoter.user.external_id))
    )
    return render(
        request,
        "web/panel/data.html",
        _panel_ctx(
            request,
            promoter,
            "data",
            profile=profile,
            address=addr,
            phone_fmt=panel_data._fmt_phone(getattr(profile, "phone", None)),
            cpf_fmt=_fmt_cpf(getattr(profile, "cpf", None)),
            pix=panel_data.pix_account(promoter.user),
            files=panel_data.files(promoter.user),
        ),
    )


@panel_route
def panel_file(request, promoter, key: str):
    """Visualizador do arquivo: página grande + o que a leitura automática extraiu."""
    item = next((f for f in panel_data.files(promoter.user) if f["key"] == key), None)
    if item is None:
        return HttpResponse(status=204)
    return render(request, "web/panel/_file_modal.html", {"file": item})


def _fmt_cpf(cpf: str | None) -> str:
    d = "".join(c for c in (cpf or "") if c.isdigit())
    return f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}" if len(d) == 11 else (cpf or "—")


@require_POST
@htmx_action
def panel_invite(request):
    uid = _uid(request)
    promoter = promoter_iface.get_by_user_external_id(uid)
    if promoter is None:
        return _go_current(request)
    phone = _digits(request.POST.get("phone"))
    problem = _phone_problem(phone)
    if problem:
        return _feedback(
            request,
            tone="danger",
            title="Confere o número do aluno?",
            text=problem,
            shake=True,
        )
    result = promoter_iface.invite_lead(
        promoter=promoter, phone=phone, cpf=_digits(request.POST.get("cpf")) or None
    )
    # o botão "Falar com o aluno" apontava pra `wa.me/` sem número: abria o WhatsApp em branco
    return render(
        request,
        "web/panel/_invite_ok.html",
        {
            "result": result,
            "wa_url": (
                "https://wa.me/55"
                + phone
                + "?text="
                + quote(
                    "Oi! Comecei sua matrícula no supletivo — é só abrir o link que "
                    "te mandei e continuar por lá."
                )
            ),
        },
    )


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


def _closing_parts(iso: str) -> dict:
    """ISO → {mes:'JUL', dia:'31', hora:'18h'} pro tile-calendário do hero (protótipo A5.1)."""
    from datetime import datetime

    _M = (
        "JAN",
        "FEV",
        "MAR",
        "ABR",
        "MAI",
        "JUN",
        "JUL",
        "AGO",
        "SET",
        "OUT",
        "NOV",
        "DEZ",
    )
    try:
        dt = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return {}
    return {"mes": _M[dt.month - 1], "dia": dt.day, "hora": f"{dt.hour}h"}


def _hub_label(promoter) -> str:
    """Nome do polo pro badge do header do painel (protótipo: 'Promotor <marca>')."""
    hub = getattr(promoter, "hub", None)
    brand = (getattr(hub, "brand", "") or "").replace("_", " ").strip()
    return f"Promotor {brand.title()}" if brand else "Promotor"


def _short_date(iso: str) -> str:
    """ISO (data ou datetime) → '25 jul' — o intervalo do ciclo no hero (protótipo A5.1)."""
    from datetime import datetime

    _M = (
        "jan",
        "fev",
        "mar",
        "abr",
        "mai",
        "jun",
        "jul",
        "ago",
        "set",
        "out",
        "nov",
        "dez",
    )
    try:
        dt = datetime.fromisoformat(str(iso))
    except (TypeError, ValueError):
        return str(iso)
    return f"{dt.day} {_M[dt.month - 1]}"


# estado REAL do ciclo pro chip do hero (protótipo A5.1: aberto · processando · pago). Antes o
# chip era o literal "ciclo aberto" no template — mentia depois do fechamento de sexta, e mentir
# sobre pagamento é o pior bug possível num painel de comissão.
_CYCLE_LABEL = {
    "open": ("ciclo aberto", "b-warn"),
    "processing": ("enviando o Pix", "b-info"),
    "paid": ("pago", "b-ok"),
    "failed": ("falhou — o polo já foi avisado", "b-danger"),
}


def _cycle_state(user, summary: dict) -> dict:
    """Lê a `PaymentRequest` da semana do promotor; sem ela, o ciclo está aberto."""
    from finance.models import PaymentRequest

    state = "open"
    try:
        pr = (
            PaymentRequest.objects.filter(payee=user)
            .order_by("-created_at")
            .values_list("status", flat=True)
            .first()
        )
        if pr == PaymentRequest.Status.PAID:
            state = "paid"
        elif pr == PaymentRequest.Status.FAILED:
            state = "failed"
        elif pr:
            state = "processing"
    except Exception:  # noqa: BLE001 — painel nunca cai por causa do chip
        state = "open"
    label, badge = _CYCLE_LABEL[state]
    return {"state": state, "label": label, "badge": badge}
