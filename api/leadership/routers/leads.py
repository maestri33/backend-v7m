"""Router de Leads do Polo (Coordenador de Polo)."""

from __future__ import annotations

from ninja import Router

from api.auth import require_roles
from api.leadership.schemas import HubLeadDetailOut, HubLeadRowOut
from hub import interface as hub_iface
from users.auth.models import User
from users.exceptions import Forbidden, NotFound
from users.roles.lead import service as lead_iface

router = Router(tags=["lead"])

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


@router.get("/leads", response=list[HubLeadRowOut], summary="Listagem de leads do polo")
def list_hub_leads(request, status: str | None = None):
    """Lista os leads do polo do coordenador."""
    coordinator = _coordinator(request)
    hub = _coordinator_hub(coordinator)
    leads = lead_iface.list_leads(hub=hub, status=status)
    return [lead_iface.lead_to_dict(lead) for lead in leads]


@router.get("/leads/{external_id}", response=HubLeadDetailOut, summary="Detalhe de lead do polo")
def get_hub_lead(request, external_id: str):
    """Detalhe completo de um lead do polo."""
    coordinator = _coordinator(request)
    hub = _coordinator_hub(coordinator)
    lead = lead_iface.get_lead_for_hub(external_id=external_id, hub=hub)
    if lead is None:
        raise NotFound("Lead não encontrado neste polo.", code="LEAD_NOT_FOUND")
    return lead_iface.lead_self_dict(lead)
