"""System checks do app `users`.

- `users.E001` (Error): o catálogo de roles (`ROLE_RULES`) não tem nenhuma role de ENTRADA
  (`from_role=None`) → ninguém consegue se registrar. Trava o boot (config quebrada).
- `users.W001` (Warning): WhatsApp não configurado → o OTP (mecanismo de login) não tem como ser
  enviado. Não trava (o whatsapp tem seu próprio E001/E002); aqui só lembra que o auth depende dele.
"""

from django.conf import settings
from django.core.checks import Error
from django.core.checks import Warning as DjangoWarning


def check_users(app_configs, **kwargs):
    errors = []

    from users.roles import catalog

    if not any(r.from_role is None for r in catalog.all_rules()):
        errors.append(
            Error(
                "ROLE_RULES não tem nenhuma role de entrada (from_role=None) — o register não "
                "consegue atribuir papel inicial.",
                hint="Inclua ao menos uma regra com from_role null no ROLE_RULES do .env.",
                id="users.E001",
            )
        )

    if not (
        getattr(settings, "WHATSAPP_API_BASE_URL", "")
        and getattr(settings, "WHATSAPP_API_KEY", "")
    ):
        errors.append(
            DjangoWarning(
                "WhatsApp não configurado — o OTP (login passwordless do auth) não tem canal de envio.",
                hint="Configure WHATSAPP_API_BASE_URL/WHATSAPP_API_KEY.",
                id="users.W001",
            )
        )

    return errors
