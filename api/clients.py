"""Grupo `clients` (PLACEHOLDER) — público do funil do ALUNO (**$$ ENTRA**):
lead → enrollment → student → veteran.

Fatia 6a (LEAD): captação pública (cria lead + checkout, devolve o pagamento na hora), check/login
por OTP. Casca fina (CONVENTION §3): valida a borda e chama o `interface/` in-process; zero regra aqui.
O funil autenticado da matrícula (enrollment) entra na 6b.
"""

from __future__ import annotations

from typing import Literal

from ninja import Field, File, Router, Schema
from ninja.files import UploadedFile

from api.auth import require_roles
from api.base import add_auth_refresh, build_group, resolve_rg_slot
from api.schemas import TokenOut
from users.auth import interface as auth_iface
from users.auth.models import User
from users.exceptions import Forbidden, NotFound
from users.roles import interface as roles
from users.roles.enrollment import interface as enrollment_iface
from users.roles.lead import interface as lead_iface
from users.roles.student import interface as student_iface

# Registry de `code` de erro (proposta API #5): TODO 4xx sai `{detail, code, …extra}` — o front
# roteia por `switch(code)`, nunca parseando `detail`. Vai na descrição do grupo → OpenAPI.
_ERROR_REGISTRY = """
### Códigos de erro (`{detail, code, …extra}`)

| code | quando | extras |
|---|---|---|
| `WRONG_STATUS` | ação fora da etapa do wizard (409) | `expected_status` (etapa a abrir), `missing_fields` (se faltam campos do RG/perfil) |
| `VALIDATION_ERROR` | body/query fora do schema (422) | `detail` = lista do pydantic |
| `SLOT_INVALID` | slot de foto desconhecido (422) | — |
| `MISSING_FIELD` | faltou cpf/phone/external_id no check (422) | — |
| `CPF_EXISTS` / `PHONE_EXISTS` / `EMAIL_EXISTS` | cadastro duplicado (409) | — |
| `CPF_INVALID` / `PHONE_INVALID` / `CPF_NOT_FOUND` | dado rejeitado na validação (422) | — |
| `CPF_SERVICE_DOWN` / `PHONE_SERVICE_DOWN` / `CEP_SERVICE_DOWN` | serviço externo fora (502) | — |
| `CEP_NOT_FOUND` / `STATE_INVALID` | endereço inválido (422) | — |
| `ENROLLMENT_NOT_FOUND` / `LEAD_NOT_FOUND` / `CHECKOUT_NOT_FOUND` / `USER_NOT_FOUND` / `STUDENT_NOT_FOUND` / `ADDRESS_NOT_FOUND` | recurso não existe (404) | — |
| `UNAUTHORIZED` / `SESSION_EXPIRED` | sem token ou token vencido (401) | — |
| `FORBIDDEN_ROLE` / `NOT_IN_FUNNEL` | papel sem acesso à rota (403) | — |
| `RATE_LIMITED` | espera do OTP (429) | `retry_after_s` |
| `ERROR` | fallback (erro sem code próprio) | — |
"""

api = build_group(
    "clients",
    "Funil do aluno: lead, enrollment, student, veteran.\n" + _ERROR_REGISTRY,
)

# roles do funil do aluno, mais avançada primeiro (login emite JWT com TODAS as ativas).
_FUNNEL_ROLES = ("veteran", "student", "enrollment", "lead")


# ── schemas ──────────────────────────────────────────────────────────────
class LeadCreateIn(Schema):
    cpf: str
    phone: str
    email: str
    payment_method: str | None = None  # default cartão (resolvido no service)
    ref: str | None = None  # external_id do promotor (landing ?ref=)


class CheckoutOut(Schema):
    payment_method: str
    provider: str
    amount: str
    is_paid: bool
    checkout_url: str | None = None
    short_url: str | None = None  # link curto no nosso domínio (manda por WhatsApp)
    qrcode_payload: str | None = None
    qrcode_image: str | None = None
    due_date: str | None = None


class LeadOut(Schema):
    external_id: str = Field(
        description="external_id do LEAD (≠ do user — proposta #8)"
    )
    user_external_id: str = Field(
        description="external_id do USER — é o que o POST /auth/login espera (proposta #8). "
        "O register cria o lead E o user na mesma transação; o login é por USER, não por lead."
    )
    status: str
    checkout: CheckoutOut | None = None


class CheckIn(Schema):
    cpf: str | None = None
    phone: str | None = None
    external_id: str | None = (
        None  # re-dispara OTP de usuário já conhecido (o service já aceitava)
    )


class CheckOut(Schema):
    found: bool
    external_id: str | None = Field(
        None,
        description="external_id do USER (é o que o /auth/login espera — proposta #8)",
    )
    otp_sent: bool
    otp_wait: int | None = None
    whatsapp: bool | None = None
    roles: list[str] | None = None


class LoginIn(Schema):
    external_id: str = Field(description="external_id do USER (veio do /auth/check)")
    otp: str


class CardPriceOut(Schema):
    installments: int
    installment: str  # valor da parcela em reais (string), ex.: "99.00"
    total: str  # valor cheio em reais (string), ex.: "1188.00"


class PricingOut(Schema):
    pix: str  # valor cheio do PIX em reais (string), ex.: "999.00"
    card: CardPriceOut


class UrlOut(Schema):
    url: str


class LeadCustomerOut(Schema):
    name: str | None = None
    phone: str | None = None
    email: str | None = None
    cpf: str | None = None


class LeadPromoterOut(Schema):
    external_id: str = Field(
        description="external_id do USER do promotor (o mesmo do `?ref=` da landing — proposta #8)"
    )
    name: str | None = None


class LeadSelfCheckoutOut(Schema):
    payment_method: str
    provider: str
    amount: str
    is_paid: bool
    checkout_url: str | None = None
    url: str | None = None  # ✦ URL única: checkout se não pagou, recibo se pagou
    receipt_url: str | None = None
    qrcode_payload: str | None = None
    qrcode_image: str | None = None
    due_date: str | None = None


class LeadMeOut(Schema):
    external_id: str = Field(
        description="external_id do LEAD (≠ do user — proposta #8)"
    )
    status: str = Field(description="pending | paid | failed")
    failed_reason: str | None = None
    created_at: str
    customer: LeadCustomerOut
    promoter: LeadPromoterOut
    checkout: LeadSelfCheckoutOut | None = None


class AddressOut(Schema):
    cep: str | None = None
    zipcode: str | None = Field(
        None, description="DEPRECATED — use `cep` (alias temporário)"
    )
    street: str | None = None
    number: str | None = None
    complement: str | None = None
    neighborhood: str | None = None
    city: str | None = None
    state: str | None = None
    country: str | None = None
    missing_fields: list[str] = Field(
        default=[],
        description='O que ainda falta preencher (plan/13): ["number"] = ViaCEP achou tudo, '
        "só falta o número; rua/bairro na lista = cidade de CEP único (digitar no PATCH)",
    )


class StudentPlatformOut(Schema):
    url: str | None = None
    login: str | None = None
    password: str | None = None
    notes: str | None = None


class StudentDocumentOut(Schema):
    doc_type: str
    validation_status: str
    has_photo: bool


class PendencyOut(Schema):
    external_id: str = Field(description="external_id da PENDÊNCIA (proposta #8)")
    kind: str
    description: str | None = None
    amount_cents: int | None = None


class StudentPendencyOut(PendencyOut):
    resolved: bool


class StudentDiplomaOut(Schema):
    issued_at: str | None = None
    picked_up: bool


class StudentMeOut(Schema):
    external_id: str = Field(
        description="external_id do STUDENT (≠ do user, ≠ da matrícula — proposta #8)"
    )
    status: str = Field(
        description="awaiting_documents | documents_under_review | exam_released | exam_scheduled "
        "| exam_failed | awaiting_documentation_dispatch | pending | awaiting_diploma_issuance "
        "| awaiting_pickup | veteran"
    )
    hub_external_id: str
    blood_type: str | None = None
    platform: StudentPlatformOut
    documents: list[StudentDocumentOut]
    pendencies: list[StudentPendencyOut]
    diploma: StudentDiplomaOut | None = None


# Erros de domínio (`DomainError`, incl. os XxxError dos services) NÃO são capturados aqui:
# sobem pro handler central da fábrica (`api/base.py`) → JSON `{detail, code, …extra}` no status certo.


# ── preço de vitrine (público) — o front exibe na landing ────────────────────
@api.get("/pricing", response=PricingOut, auth=None, tags=["pricing"])
def pricing(request):
    """Preço de VITRINE público (sem login): PIX (valor cheio) + cartão em 12x. ≠ a cobrança real."""
    return lead_iface.pricing()


# ── clients/auth — entrada do cliente (pública): cadastro + login por OTP ─────
# Victor 2026-06-07: captação/login são ENTRADA → vivem em /auth. TODO cliente entra como `lead`.
auth_router = Router(tags=["auth"])
lead_router = Router(tags=["lead"])


@auth_router.post("/register", response={201: LeadOut}, auth=None)
def register(request, payload: LeadCreateIn):
    """Cadastro do cliente: **TODO cliente entra OBRIGATORIAMENTE como `lead`.** Cria o lead (cpf/phone/
    email + método) + o checkout e devolve o pagamento na hora."""
    result = lead_iface.create_lead(
        cpf=payload.cpf,
        phone=payload.phone,
        email=payload.email,
        payment_method=payload.payment_method,
        ref=payload.ref,
    )
    return 201, result


@auth_router.post("/check", response=CheckOut, auth=None)
def check(request, payload: CheckIn):
    """Dispara OTP por cpf/phone e **VAZA existência** (CONVENTION §5): devolve `found`+`roles` honestos —
    o front decide cadastro novo × login e pra qual fase do funil mandar."""
    return auth_iface.check(
        cpf=payload.cpf, phone=payload.phone, external_id=payload.external_id
    )


@auth_router.post("/login", response=TokenOut, auth=None)
def login(request, payload: LoginIn):
    """Login passwordless (OTP) — resolve o papel mais avançado do funil do cliente (lead→enrollment→
    student; veteran exige student) e emite JWT com TODAS as roles ativas."""
    user = User.objects.filter(external_id=payload.external_id).first()
    if user is None:
        raise NotFound("Usuário não encontrado.", code="USER_NOT_FOUND")
    active = roles.active_roles(user)
    funnel_role = next((r for r in _FUNNEL_ROLES if r in active), None)
    if funnel_role is None:
        raise Forbidden(
            "Usuário não faz parte do funil do aluno.", code="NOT_IN_FUNNEL"
        )
    return auth_iface.login(
        external_id=payload.external_id, role=funnel_role, otp=payload.otp
    )


add_auth_refresh(auth_router)


# ── clients/lead — a fase LEAD do funil: estado + a URL (leitura do PRÓPRIO dado) ─
def _lead_guard(request):
    """Devolve o lead do usuário logado (404 se não houver). Aceita QUALQUER role do funil do aluno:
    pós-promoção (lead→enrollment→…) o cliente continua vendo o próprio checkout/recibo — antes dava
    403 e quem pagou não via o recibo (auditoria do front 2026-06-10)."""
    require_roles(request.auth, *_FUNNEL_ROLES)
    lead = lead_iface.get_for_user_external_id(request.auth.external_id)
    if lead is None:
        raise NotFound("Lead não encontrado.", code="LEAD_NOT_FOUND")
    return lead


@lead_router.get("/me", response=LeadMeOut)
def lead_me(request):
    """TODOS os dados do lead do cliente logado, incl. a URL (✦ checkout se não pagou / recibo se pagou)."""
    return lead_iface.lead_self_dict(_lead_guard(request))


@lead_router.get("/checkout-url", response=UrlOut)
def lead_checkout_url(request):
    """Só a URL de pagamento/recibo do lead (link único ✦ que redireciona checkout↔recibo)."""
    url = lead_iface.checkout_url_for(_lead_guard(request))
    if url is None:
        raise NotFound("Checkout não encontrado.", code="CHECKOUT_NOT_FOUND")
    return {"url": url}


api.add_router("/auth", auth_router)
api.add_router("/lead", lead_router)


# ── matrícula: funil de coleta (autenticado, role enrollment) — 6b ──────────
# Fluxo plan/13 (Victor 2026-06-11): DOCUMENTO primeiro (a IA extrai e povoa o perfil) →
# endereço (POST só com CEP) → educação → selfie (= ASSINATURA da matrícula) → liberação.
# Convenção: as seções devolvem `missing_fields` — o front renderiza input SÓ do que falta.
class AddressCepIn(Schema):
    cep: str


class AddressDataIn(Schema):
    # PATCH — o backend só preenche os que estão VAZIOS (não sobrescreve o que o CEP trouxe).
    street: str | None = None
    number: str | None = None
    complement: str | None = None
    neighborhood: str | None = None
    city: str | None = None
    state: str | None = None


class EducationIn(Schema):
    level: str  # 'fundamental' | 'medio'
    grade: int  # 1–9 (fundamental) / 1–3 (médio) — validado no service por nível
    completed: bool  # concluiu o nível?
    last_school: str
    city: str  # cidade da escola
    state: str  # UF da escola
    last_year_when: str | None = None


# enums canônicos no OpenAPI (proposta #6): em vez de `"string"`, o schema declara os valores.
AnalysisStatus = Literal["pending", "approved", "rejected", "review"]
WizardStatus = Literal[
    "rg", "address", "education", "selfie", "awaiting_release", "completed"
]


class EnrollmentOut(Schema):
    external_id: str = Field(
        description="external_id da MATRÍCULA (≠ do user, ≠ do promoter) — proposta #8"
    )
    status: WizardStatus = Field(
        description="Seção do wizard a preencher AGORA: rg (documento) | address | education "
        "| selfie | awaiting_release | completed"
    )
    hub_external_id: str
    selfie_verified: bool
    # status canônico unificado da análise (proposta #4): a análise da SELFIE/assinatura.
    analysis_status: AnalysisStatus | None = Field(
        None, description="Análise da selfie: pending | approved | rejected | review"
    )
    selfie_status: str = Field(
        description="[DEPRECATED — use analysis_status] alias de compat"
    )
    # ack de polling (proposta #2): preenchidos SÓ na resposta do POST /selfie (que dispara análise).
    poll_after_ms: int | None = Field(
        None,
        description="Quando o front deve voltar a perguntar (ms). None fora de mutação.",
    )
    expires_at: str | None = Field(
        None,
        description="Até quando o `pending` vale; depois disso vira `review` (TTL).",
    )


class RgSectionOut(Schema):
    """Seção DOCUMENTO completa (GET/PATCH /enrollment/documents/rg) — plan/13."""

    # editáveis no PATCH (completa/corrige o que a extração não trouxe)
    number: str | None = None
    issuing_agency: str | None = None
    issue_date: str | None = None
    mother_name: str | None = None
    father_name: str | None = None
    birthplace: str | None = None
    marital_status: str | None = None
    nationality: str | None = None
    # do CPFHub/extração — NÃO editáveis pelo aluno
    name: str | None = None
    birth_date: str | None = None
    # fotos + validação por IA
    front_photo: str | None = None
    back_photo: str | None = None
    full_photo: str | None = None
    # canônico unificado (proposta #4): `analysis_status`/`analysis_reason`.
    analysis_status: AnalysisStatus | None = Field(
        None,
        description="pending (analisando) | approved | rejected (refazer — motivo em "
        "analysis_reason) | review (coordenador vai decidir)",
    )
    analysis_reason: str | None = None  # o PORQUÊ (a IA sempre justifica)
    validation_status: AnalysisStatus | None = Field(
        None, description="[DEPRECATED — use analysis_status] alias de compat"
    )
    validation_reason: str | None = None  # [DEPRECATED — use analysis_reason]
    missing_fields: list[str] = []  # o aluno completa SÓ esses (no PATCH)


class RgPatchIn(Schema):
    number: str | None = None
    issuing_agency: str | None = None
    issue_date: str | None = None  # AAAA-MM-DD
    mother_name: str | None = None
    father_name: str | None = None
    birthplace: str | None = None
    marital_status: str | None = None
    nationality: str | None = None


class SelfieOut(Schema):
    """GET /enrollment/selfie — a selfie é a ASSINATURA da matrícula (plan/13)."""

    exists: bool
    photo: str | None = None
    taken_at: str | None = None
    # canônico unificado (proposta #4): `analysis_status`/`analysis_reason` + `expires_at` (TTL #2).
    analysis_status: AnalysisStatus | None = Field(
        None, description="pending (analisando) | approved | rejected | review"
    )
    analysis_reason: str | None = None  # comentários da IA + instruções p/ ser aprovada
    expires_at: str | None = Field(
        None, description="Até quando o `pending` vale; depois vira `review` (TTL)."
    )
    status: AnalysisStatus | None = Field(
        None, description="[DEPRECATED — use analysis_status] alias de compat"
    )
    verified: bool = False
    description: str | None = None  # [DEPRECATED — use analysis_reason]


class EnrollmentProfileOut(Schema):
    mother_name: str | None = None
    father_name: str | None = None
    marital_status: str | None = None
    birthplace: str | None = None
    nationality: str | None = None


class RgOut(Schema):
    number: str | None = None
    issuing_agency: str | None = None
    issue_date: str | None = None
    front_photo: str | None = None
    back_photo: str | None = None
    full_photo: str | None = (
        None  # RG inteiro (frente+verso numa imagem) — alternativa ao par
    )
    # canônico unificado (proposta #4): `analysis_status`/`analysis_reason`.
    analysis_status: AnalysisStatus | None = Field(
        None,
        description="Validação por IA (plan/12): pending (analisando) | approved | rejected "
        "(refazer — motivo em analysis_reason) | review (coordenador vai decidir)",
    )
    analysis_reason: str | None = None  # o PORQUÊ do status (a IA sempre justifica)
    validation_status: AnalysisStatus | None = Field(
        None, description="[DEPRECATED — use analysis_status] alias de compat"
    )
    validation_reason: str | None = None  # [DEPRECATED — use analysis_reason]
    missing_fields: list[str] = []  # campos que o OCR não leu — o aluno digita só esses


class EducationOut(Schema):
    level: str | None = None
    grade: int | None = None
    completed: bool | None = None
    last_school: str | None = None
    city: str | None = None
    state: str | None = None
    last_year_when: str | None = None


class EnrollmentMeOut(EnrollmentOut):
    """Resposta CANÔNICA da matrícula (proposta #3): o /me e TODA mutação que mexe na máquina
    devolvem este shape completo — o front roteia pelo `status` sem re-fetch. Bloco None = vazio."""

    profile: EnrollmentProfileOut | None = None
    address_complete: bool = Field(
        description="[DEPRECATED — use address.missing_fields]"
    )
    address: AddressOut | None = None
    rg: RgOut | None = None
    education: EducationOut | None = None
    selfie: SelfieOut | None = None


def _enr_guard(request) -> str:
    """Gate role enrollment + devolve o external_id do aluno logado."""
    require_roles(request.auth, "enrollment")
    return request.auth.external_id


@api.get("/enrollment/me", response=EnrollmentMeOut, tags=["enrollment"])
def enrollment_me(request):
    """Estado COMPLETO da matrícula pro resume do wizard: status + cada seção já preenchida, numa chamada.

    Aceita também o STUDENT (plan/14, Victor 2026-06-12): depois de concluída, o aluno continua
    enxergando todos os dados da matrícula. A fase da taxa NUNCA aparece aqui (status mascarado —
    política interna do polo)."""
    require_roles(request.auth, "enrollment", "student")
    enr = enrollment_iface.get_for_user_external_id(request.auth.external_id)
    if enr is None:
        raise NotFound("Matrícula não encontrada.", code="ENROLLMENT_NOT_FOUND")
    return enrollment_iface.me_dict(enr)


# ── seção DOCUMENTO (primeira do wizard — plan/13) ───────────────────────────


class RgUploadAck(Schema):
    """Ack do upload do RG (proposta #2): a análise roda em 2º plano; o front sabe quando voltar a
    perguntar (`poll_after_ms`) e até quando o `pending` vale (`expires_at`, depois vira `review`)."""

    slot: Literal["front", "back", "full"]
    stored: str
    analysis_status: AnalysisStatus
    poll_after_ms: int
    expires_at: str | None = None
    analysis: str = Field(
        description="[DEPRECATED — use analysis_status] alias de compat"
    )


@api.post(
    "/enrollment/documents/rg/photo/{slot}", response=RgUploadAck, tags=["enrollment"]
)
def enrollment_rg_photo(request, slot: str, file: UploadedFile = File(...)):
    """Foto do RG — `slot` aceita **`front`**, **`back`** ou **`full`** (documento inteiro numa
    imagem). Arquivo: JPEG/PNG/WEBP ou **PDF** (convertido internamente). A análise por IA roda
    em 2º plano: acompanhe `analysis_status` (+ motivo e campos extraídos) no
    `GET /enrollment/documents/rg`, voltando a perguntar a cada `poll_after_ms`."""
    ext = _enr_guard(request)
    real_slot = resolve_rg_slot(slot)
    ack = enrollment_iface.upload_rg_photo(
        user_external_id=ext, slot=real_slot, upload=file
    )
    return {"slot": slot, "analysis": ack["analysis_status"], **ack}


@api.get("/enrollment/documents/rg", response=RgSectionOut, tags=["enrollment"])
def enrollment_rg_get(request):
    """Seção documento completa: fotos, validação (status+motivo), TODOS os campos extraídos
    pela IA (doc + filiação/naturalidade/nascimento) e `missing_fields` (o que falta digitar)."""
    ext = _enr_guard(request)
    return enrollment_iface.get_rg_section(user_external_id=ext)


@api.patch("/enrollment/documents/rg", response=EnrollmentMeOut, tags=["enrollment"])
def enrollment_rg_patch(request, payload: RgPatchIn):
    """Completa/CORRIGE manualmente o que a extração não trouxe (`missing_fields`): campos do
    documento (número/órgão/emissão) e do perfil (filiação/naturalidade/estado civil/nacionalidade).
    Devolve o **EnrollmentMe canônico** (proposta #3) — o detalhe do RG segue no GET da seção."""
    ext = _enr_guard(request)
    return enrollment_iface.patch_rg_section(
        user_external_id=ext, **payload.dict(exclude_none=True)
    )


# ── seção ENDEREÇO (plan/13: POST só com CEP) ────────────────────────────────
@api.get("/enrollment/address", response=AddressOut, tags=["enrollment"])
def enrollment_get_address(request):
    """GET do endereço + `missing_fields` (o que ainda falta preencher)."""
    ext = _enr_guard(request)
    return enrollment_iface.get_address(user_external_id=ext)


@api.post("/enrollment/address", response=EnrollmentMeOut, tags=["enrollment"])
def enrollment_address(request, payload: AddressCepIn):
    """Body só `{cep}`: acha no ViaCEP, grava o endereço e devolve o **EnrollmentMe canônico**
    (proposta #3) — `address.missing_fields` JÁ AVISA o que falta: `["number"]` = achou tudo, só
    falta o número; rua/bairro na lista = cidade de CEP único (digite no PATCH)."""
    ext = _enr_guard(request)
    return enrollment_iface.set_address_cep(user_external_id=ext, cep=payload.cep)


@api.patch("/enrollment/address", response=EnrollmentMeOut, tags=["enrollment"])
def enrollment_address_patch(request, payload: AddressDataIn):
    """Preenche os demais campos — SÓ os que estão VAZIOS (não sobrescreve o que o CEP trouxe).
    Devolve o EnrollmentMe canônico (proposta #3)."""
    ext = _enr_guard(request)
    return enrollment_iface.set_address_data(
        user_external_id=ext, **payload.dict(exclude_none=True)
    )


# ── seção EDUCAÇÃO ───────────────────────────────────────────────────────────
@api.get("/enrollment/education", response=EducationOut, tags=["enrollment"])
def enrollment_get_education(request):
    """GET dos dados educacionais (tudo None = ainda não preenchido)."""
    ext = _enr_guard(request)
    return enrollment_iface.get_education(user_external_id=ext)


@api.post("/enrollment/education", response=EnrollmentMeOut, tags=["enrollment"])
def enrollment_education(request, payload: EducationIn):
    """Grava os dados escolares e devolve o **EnrollmentMe canônico** (proposta #3) — o corpo já
    traz o novo `status` (ex.: `selfie`), sem GET extra."""
    ext = _enr_guard(request)
    enr = enrollment_iface.set_education(
        user_external_id=ext,
        level=payload.level,
        grade=payload.grade,
        completed=payload.completed,
        last_school=payload.last_school,
        city=payload.city,
        state=payload.state,
        last_year_when=payload.last_year_when,
    )
    return enrollment_iface.me_dict(enr)


# ── seção SELFIE (= a ASSINATURA da matrícula — plan/13) ─────────────────────
@api.get("/enrollment/selfie", response=SelfieOut, tags=["enrollment"])
def enrollment_get_selfie(request):
    """Foto, quando foi enviada, status da análise e os comentários da IA/biometria (inclusive
    instruções de como ser aprovada, se reprovou). `exists: false` = ainda não enviada."""
    ext = _enr_guard(request)
    return enrollment_iface.get_selfie(user_external_id=ext)


@api.post("/enrollment/selfie", response=EnrollmentMeOut, tags=["enrollment"])
def enrollment_selfie(request, file: UploadedFile = File(...)):
    """Envia a selfie (assinatura). A análise roda em 2º plano (IA + biometria vs rosto do
    DOCUMENTO): acompanhe `analysis_status` no `GET /enrollment/selfie` (`pending` = analisando),
    voltando a perguntar a cada `poll_after_ms` (a resposta já traz o ack — proposta #2) e o
    **EnrollmentMe canônico** (proposta #3)."""
    ext = _enr_guard(request)
    enr = enrollment_iface.set_selfie(
        user_external_id=ext,
        image_bytes=file.read(),
        content_type=getattr(file, "content_type", "image/jpeg"),
    )
    return {**enrollment_iface.me_dict(enr), **enrollment_iface.selfie_ack(enr)}


# ── aluno: funil final student→veteran (autenticado, role student) — §4 item 9 ──
# ⚠️ NÃO TESTADO (nem in-process completo, nem com aluno/IA real).
class BloodTypeIn(Schema):
    blood_type: str  # A+/A-/B+/B-/AB+/AB-/O+/O-


class ExamScheduleIn(Schema):
    subject: str
    scheduled_at: str  # ISO 8601 (ex.: 2026-06-10T14:00:00-03:00)


def _student_guard(request) -> str:
    """Gate role student + devolve o external_id do aluno logado."""
    require_roles(request.auth, "student")
    return request.auth.external_id


def _student_dict(ext: str):
    s = student_iface.get_for_user_external_id(ext)
    if s is None:
        raise NotFound("Aluno não encontrado.", code="STUDENT_NOT_FOUND")
    return student_iface.to_dict(s)


@api.get("/student/me", response=StudentMeOut, tags=["student"])
def student_me(request):
    return _student_dict(_student_guard(request))


@api.post("/student/blood-type", response=StudentMeOut, tags=["student"])
def student_blood_type(request, payload: BloodTypeIn):
    ext = _student_guard(request)
    student_iface.set_blood_type(user_external_id=ext, blood_type=payload.blood_type)
    return _student_dict(ext)


@api.post("/student/documents/{doc_type}", response=StudentMeOut, tags=["student"])
def student_document(request, doc_type: str, file: UploadedFile = File(...)):
    ext = _student_guard(request)
    student_iface.upload_document(
        user_external_id=ext,
        doc_type=doc_type,
        image_bytes=file.read(),
        content_type=getattr(file, "content_type", "image/jpeg"),
    )
    return _student_dict(ext)


@api.post("/student/exam/schedule", response=StudentMeOut, tags=["student"])
def student_exam_schedule(request, payload: ExamScheduleIn):
    ext = _student_guard(request)
    student_iface.schedule_exam(
        user_external_id=ext,
        subject=payload.subject,
        scheduled_at=payload.scheduled_at,
    )
    return _student_dict(ext)


@api.get("/student/pendencies", response=list[PendencyOut], tags=["student"])
def student_pendencies(request):
    ext = _student_guard(request)
    pends = student_iface.list_pendencies(ext, open_only=True)
    return [
        {
            "external_id": str(p.external_id),
            "kind": p.kind,
            "description": p.description,
            "amount_cents": p.amount_cents,
        }
        for p in pends
    ]


@api.post("/student/diploma/pickup", response=StudentMeOut, tags=["student"])
def student_diploma_pickup(request, file: UploadedFile = File(...)):
    """Aluno posta a foto tirando o diploma → vira veteran + dispara a comissão do coordenador."""
    ext = _student_guard(request)
    student_iface.register_pickup(
        user_external_id=ext,
        image_bytes=file.read(),
        content_type=getattr(file, "content_type", "image/jpeg"),
    )
    return _student_dict(ext)
