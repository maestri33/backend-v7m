"""Router de Promotores do Polo (Coordenador de Polo)."""

from __future__ import annotations

from ninja import Router

from api.auth import require_roles
from api.leadership.schemas import HubPromoterRowOut, MaterialApproveOut
from hub import interface as hub_iface
from users.auth.models import User
from users.exceptions import Forbidden
from users.roles.promoter import service as promoter_iface
from users.roles.training import service as training_iface

router = Router(tags=["promoter"])

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


@router.get("/promoters", response=list[HubPromoterRowOut], summary="Listagem de promotores do polo")
def list_hub_promoters(request):
    """Lista promotores do polo e status de trava de treino."""
    coordinator = _coordinator(request)
    hub = _coordinator_hub(coordinator)
    return promoter_iface.list_for_hub(hub)


@router.post("/promoters/{external_id}/suspend", response=HubPromoterRowOut, summary="Suspender promotor")
def suspend_promoter(request, external_id: str):
    """Suspende promotor do polo."""
    coordinator = _coordinator(request)
    p = promoter_iface.suspend(user_external_id=external_id, coordinator=coordinator)
    from users.profiles import interface as profiles

    profile = profiles.get(p.user)
    return {
        "external_id": external_id,
        "name": profile.name if profile else None,
        "status": p.status,
        "locked": False,
    }


@router.post("/promoters/{external_id}/reactivate", response=HubPromoterRowOut, summary="Reativar promotor")
def reactivate_promoter(request, external_id: str):
    """Reativa promotor suspenso."""
    coordinator = _coordinator(request)
    p = promoter_iface.reactivate(user_external_id=external_id, coordinator=coordinator)
    from users.profiles import interface as profiles

    profile = profiles.get(p.user)
    return {
        "external_id": external_id,
        "name": profile.name if profile else None,
        "status": p.status,
        "locked": False,
    }


@router.post(
    "/promoters/{external_id}/materials/{material_external_id}/approve",
    response=MaterialApproveOut,
    tags=["training"],
    summary="Aprovar matéria de promotor travado no treino",
)
def approve_open_material(request, external_id: str, material_external_id: str):
    """Coordenador aprova matéria em aberto para destravar promotor."""
    coordinator = _coordinator(request)
    return training_iface.coordinator_approve_material(
        promoter_external_id=external_id,
        material_external_id=material_external_id,
        coordinator=coordinator,
    )
