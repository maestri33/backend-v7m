"""Router de Candidatos aguardando aprovação (Coordenador de Polo)."""

from __future__ import annotations

from ninja import Router

from api.auth import require_roles
from api.leadership.schemas import (
    CandidateActionOut,
    CandidateAwaitingOut,
    CandidateDetailOut,
    RejectIn,
)
from hub import interface as hub_iface
from users.auth.models import User
from users.exceptions import Forbidden
from users.roles.candidate import service as candidate_iface

router = Router(tags=["candidate"])

NOT_COORDINATOR_DETAIL = (
    "Você não pode entrar como coordenador: não coordena nenhum polo. "
    "Faça seu login na área da sua função."
)


def _coordinator(request) -> User:
    require_roles(request.auth, "coordinator")
    user = User.objects.filter(
        external_id=request.auth.external_id, is_active=True
    ).first()
    if user is None:
        raise Forbidden("Coordenador não encontrado.", code="FORBIDDEN_ROLE")
    return user


def _coordinator_hub(coordinator: User):
    hub = hub_iface.coordinated_by(coordinator)
    if hub is None:
        raise Forbidden(NOT_COORDINATOR_DETAIL, code="NOT_HUB_COORDINATOR")
    return hub


@router.get("/candidates", response=list[CandidateAwaitingOut], summary="Candidatos aguardando aprovação")
def list_candidates_awaiting(request):
    """Fila de candidatos que concluíram coleta e aguardam aprovação."""
    coordinator = _coordinator(request)
    hub = _coordinator_hub(coordinator)
    return candidate_iface.list_awaiting_approval_for_hub(hub=hub)


@router.get("/candidates/{external_id}", response=CandidateDetailOut, summary="Detalhe de candidato para decisão")
def get_candidate_for_coordinator(request, external_id: str):
    """Detalhe do candidato para aprovação ou rejeição."""
    coordinator = _coordinator(request)
    return candidate_iface.candidate_detail_for_coordinator(
        candidate_external_id=external_id, coordinator=coordinator
    )


@router.post("/candidates/{external_id}/approve", response=CandidateActionOut, summary="Aprovar candidato (vira promotor)")
def approve_candidate(request, external_id: str):
    """Aprova candidato e promove a promotor."""
    coordinator = _coordinator(request)
    cand = candidate_iface.approve_candidate(
        candidate_external_id=external_id, coordinator=coordinator
    )
    return {"external_id": str(cand.external_id), "status": cand.status}


@router.post("/candidates/{external_id}/reject", response=CandidateActionOut, summary="Rejeitar candidato")
def reject_candidate(request, external_id: str, payload: RejectIn):
    """Rejeita candidato com motivo."""
    coordinator = _coordinator(request)
    cand = candidate_iface.reject_candidate(
        candidate_external_id=external_id,
        coordinator=coordinator,
        reason=payload.reason,
    )
    return {"external_id": str(cand.external_id), "status": cand.status}
