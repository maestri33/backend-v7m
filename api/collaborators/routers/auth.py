"""Router de autenticação do grupo Collaborators (Funil do Promotor)."""

from __future__ import annotations

from ninja import Router

from api.base import add_auth_refresh, add_funnel_login
from api.collaborators.schemas import CandidateCreateIn, CandidateJoinIn, CandidateOut
from api.schemas.auth import CheckIn, CheckOut, TokenOut
from core.webhook_auth import service_secret_ok
from users.auth.jwt import service as jwt_service
from users.auth import service as auth_iface
from users.auth import validation
from users.auth.models import User
from users.exceptions import Unauthorized
from users.profiles import interface as profiles
from users.roles import interface as roles
from users.roles.candidate import service as candidate_iface

router = Router(tags=["auth"])

FUNNEL_ROLES = ("coordinator", "promoter", "training", "candidate")


@router.post("/register", response={201: CandidateOut}, auth=None, summary="Cadastro do candidato")
def register(request, payload: CandidateCreateIn):
    """Cadastro do candidato: cria o user + Candidate ligado a um polo."""
    return 201, candidate_iface.create_candidate(
        cpf=payload.cpf, phone=payload.phone, email=payload.email, hub=payload.hub
    )


def _find_user(
    *, cpf: str | None = None, phone: str | None = None, external_id: str | None = None
):
    if external_id:
        return User.objects.filter(external_id=external_id).first()
    if cpf:
        try:
            cpf = validation.validate_cpf(cpf)
        except ValueError:
            return None
        p = profiles.find_by_cpf(cpf)
        return p.user if p else None
    if phone:
        try:
            phone = validation.validate_phone(phone)
        except ValueError:
            return None
        p = profiles.find_by_phone(phone)
        return p.user if p else None
    return None


@router.post("/check", response=CheckOut, auth=None, summary="Verificação de conta / disparo de OTP")
def check(request, payload: CheckIn):
    """Check de telefone/CPF: dispara OTP ou emite token em modo de serviço."""
    if not payload.send_otp:
        if not service_secret_ok(request):
            raise Unauthorized(
                "Segredo de serviço obrigatório para bypass de OTP.",
                code="SERVICE_SECRET_REQUIRED",
            )
        user = _find_user(
            cpf=payload.cpf, phone=payload.phone, external_id=payload.external_id
        )
        if user is not None:
            user_roles = roles.active_roles(user)
            token_data = jwt_service.issue(str(user.external_id), roles=user_roles)
            return {
                "found": True,
                "token": token_data["access_token"],
                "otp_sent": False,
                "otp_wait": None,
                "roles": user_roles,
                "external_id": str(user.external_id),
                "created": False,
            }
        return {
            "found": False,
            "token": None,
            "otp_sent": False,
            "otp_wait": None,
            "external_id": None,
            "created": False,
        }

    return auth_iface.check(
        cpf=payload.cpf,
        phone=payload.phone,
        external_id=payload.external_id,
    )


@router.post("/join", response=TokenOut, auth=None, summary="Ativação de promotor em conta existente")
def join(request, payload: CandidateJoinIn):
    """Ativa o acesso de promotor para uma conta existente após validar o OTP."""
    return candidate_iface.join_candidate(
        user_external_id=payload.external_id,
        otp=payload.otp,
        hub=payload.hub,
    )


add_funnel_login(
    router,
    funnel_roles=FUNNEL_ROLES,
    not_in_funnel_msg="Usuário não faz parte do funil do colaborador.",
)
add_auth_refresh(router)
