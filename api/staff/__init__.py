"""Grupo `staff` — administração da plataforma (superuser puro)."""

from __future__ import annotations

from api.base import build_group
from api.health import staff_health_router
from api.staff.routers.auth import router as auth_router
from api.staff.routers.finance import router as finance_router
from api.staff.routers.hubs import router as hubs_router
from api.staff.routers.materials import router as materials_router
from api.staff.routers.system import router as system_router
from api.staff.routers.users import router as users_router

api = build_group(
    "staff", "Administração da plataforma: hub, coordenador, saúde dos serviços."
)

api.add_router("/auth", auth_router)
api.add_router("", hubs_router)
api.add_router("", materials_router)
api.add_router("", finance_router)
api.add_router("", users_router)
api.add_router("", system_router)
api.add_router("", staff_health_router)

__all__ = ["api"]
