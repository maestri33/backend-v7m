"""Roteador do funil web — decide o PASSO atual do usuário e guarda as telas.

Espelha o gate do protótipo (DOCUMENTACAO A3): toda entrada de rota confere o estado real no
servidor e corrige a URL. Fonte da verdade = sessão (posse provada) + Profile + Candidate.status
+ roles ativas — nunca flag solta no cliente.
"""

from __future__ import annotations

from django.urls import reverse

from users.auth import service as auth_iface
from users.profiles import interface as profiles
from users.roles import interface as roles
from users.roles.candidate import service as candidate_iface
from users.roles.candidate.models import Candidate

SESSION_UID = "web_uid"  # user autenticado (external_id do USER)
SESSION_PENDING = "web_pending_uid"  # aguardando OTP
SESSION_PHONE = "web_phone"  # telefone mascarado pra tela de OTP
SESSION_HUB = "web_hub"  # ?hub= / ?ref= capturado na entrada (vai pro cadastro)

_S = Candidate.Status

# passo → nome de rota (web/urls.py). Ordem de exibição do stepper do wizard.
WIZARD_STEPS = ("address", "document", "pix", "education", "selfie")
STEP_LABELS = {
    "address": "Endereço",
    "document": "Documento",
    "pix": "Pix",
    "education": "Escolaridade",
    "selfie": "Selfie",
}


def capture_hub(request) -> None:
    """`?hub=` (ou `?ref=`) relaciona o candidato a um polo/promotor — capturado em QUALQUER
    entrada e preservado na sessão até o cadastro (DOCUMENTACAO: parâmetros de entrada)."""
    hub = (request.GET.get("hub") or request.GET.get("ref") or "").strip()
    if hub:
        request.session[SESSION_HUB] = hub


def user_for(request):
    """User autenticado da sessão web (ou None)."""
    uid = request.session.get(SESSION_UID)
    if not uid:
        return None
    from users.auth.models import User

    user = User.objects.filter(external_id=uid).first()
    if user is None:  # conta sumiu (purge) → sessão morta
        request.session.flush()
    return user


def current_step(request) -> str:
    """O passo REAL do usuário, olhando o servidor (roles + profile + Candidate.status)."""
    user = user_for(request)
    if user is None:
        if request.session.get(SESSION_PENDING):
            return "otp"
        return "check"

    active = roles.active_roles(user)
    if "promoter" in active or "coordinator" in active:
        from users.roles.training import service as training_iface

        return "training" if training_iface.is_locked(user) else "panel"

    profile = profiles.get(user)
    if profile is None or not profile.cpf:
        return "cpf"
    if not profile.email:
        return "email"

    # identidade completa → garante a role candidate + Candidate (idempotente; sem OTP: a posse
    # foi provada no login desta sessão).
    cand = candidate_iface.get_for_user_external_id(str(user.external_id))
    if "candidate" not in active or cand is None:
        candidate_iface.ensure_candidate(
            user_external_id=str(user.external_id),
            hub=request.session.get(SESSION_HUB) or None,
        )
        cand = candidate_iface.get_for_user_external_id(str(user.external_id))
        if cand is None:
            return "check"  # NO_HUB etc. — deixa a view de erro tratar

    # `started` → o passo "perfil" do backend não tem tela própria (estado civil/filiação vêm do
    # documento depois): avança automático pro endereço.
    if cand.status == _S.STARTED:
        candidate_iface.set_profile(user_external_id=str(user.external_id))
        cand.refresh_from_db()

    if cand.status == _S.PROFILE:
        return "address"
    if cand.status in (_S.ADDRESS, _S.DOCUMENTS):
        return "document"
    if cand.status == _S.PIX:
        return "education" if cand.pix_validated else "pix"
    if cand.status == _S.EDUCATION:
        return "selfie"
    if cand.status == _S.SELFIE:
        return "selfie"
    if cand.status in (_S.COMPLETED, _S.REJECTED):
        return "analysis"
    if cand.status == _S.APPROVED:
        return "panel"
    return "address"


def step_url(step: str) -> str:
    return reverse(f"web:{step if step != 'otp' else 'otp'}")


def resend_otp(request) -> dict:
    """Reenvio de OTP pro pendente da sessão (mesma chamada do check)."""
    pending = request.session.get(SESSION_PENDING)
    return auth_iface.check(external_id=pending)


def wizard_progress(step: str) -> list[dict]:
    """Barras do stepper (5 etapas do wizard) pra `step` atual."""
    try:
        idx = WIZARD_STEPS.index(step)
    except ValueError:
        idx = -1
    return [
        {"key": s, "label": STEP_LABELS[s], "on": i <= idx, "current": i == idx}
        for i, s in enumerate(WIZARD_STEPS)
    ]
