"""App `web` — funil WEB do candidato/promotor servido pelo próprio backend (HTMX).

Decisão (Victor 2026-07-28): antes de reconstruir o app.v7m.org (Next/mobile), o fluxo do
candidato roda server-rendered AQUI, no link do backend, pra validar o funil de ponta a ponta.
Casca fina sobre os MESMOS services de `users/` que a API Ninja usa (in-process, sem HTTP hop);
auth = sessão Django (posse provada por OTP), sem JWT no browser.
"""

from django.apps import AppConfig


class WebConfig(AppConfig):
    name = "web"
    verbose_name = "funil web (HTMX)"
