"""
URL configuration for core project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.conf import settings
from django.http import JsonResponse
from django.urls import include, path, re_path
from django.contrib import admin
from django.views.static import serve as media_serve

# API pública (Django Ninja, in-process) — 4 grupos por público, versionados sob /api/v1/.
# Nomes FIXADOS (Victor 2026-06-16): clients/collaborators/leadership/staff são definitivos.
from api.clients import api as clients_api
from api.collaborators import api as collaborators_api
from api.leadership import api as leadership_api
from api.staff import api as staff_api
from users.roles.lead.checkout_links import checkout_redirect

urlpatterns = [
    path("admin/", admin.site.urls),
    # link curto do checkout: /lead/checkout/<token> → 302 pro checkout do gateway (manda por WhatsApp).
    path("lead/checkout/<str:token>", checkout_redirect),
    # Webhooks PÚBLICOS dos gateways (chamados de fora por asaas.prod/infinitepay.prod). É a ÚNICA
    # superfície HTTP fora do Ninja que sobrou: a DMZ sem-auth (users/auth|address|documents +
    # charge/payout/status/setup) foi FECHADA e migrada pro Ninja autenticado (Victor 2026-06-16).
    path("integrations/asaas/", include("integrations.bank.asaas.urls")),
    path("integrations/infinitepay/", include("integrations.bank.infinitepay.urls")),
    # API Ninja versionada — /api/v1/<grupo>/ (cada grupo serve /docs e /openapi.json).
    path("api/v1/clients/", clients_api.urls),
    path("api/v1/collaborators/", collaborators_api.urls),
    path("api/v1/leadership/", leadership_api.urls),
    path("api/v1/staff/", staff_api.urls),
    # /media/ servido SEMPRE pelo Django neste host: notify/Evolution GO buscam
    # mídia por URL (QR, voice-note) e DEBUG agora é False (auditoria front 2026-06-11). Em prod o
    # reverse proxy pode assumir este caminho.
    re_path(
        r"^media/(?P<path>.*)$", media_serve, {"document_root": settings.MEDIA_ROOT}
    ),
]


# Host API-first: erro fora das rotas Ninja (404 de URL, 500 de view Django, bad request) responde
# JSON curto — nunca a página de debug/URLconf (auditoria front 2026-06-11). Django só usa estes
# handlers com DEBUG=False; o traceback completo continua indo pro log do server.
def _json_error(status: int, detail: str):
    def handler(request, exception=None):
        return JsonResponse({"detail": detail}, status=status)

    return handler


handler400 = _json_error(400, "Requisição inválida.")
handler403 = _json_error(403, "Acesso negado.")
handler404 = _json_error(404, "Não encontrado.")


def handler500(request):  # assinatura do Django: sem `exception`
    return JsonResponse({"detail": "Erro interno do servidor."}, status=500)
