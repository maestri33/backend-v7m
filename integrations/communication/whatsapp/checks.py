"""System checks da conexão com o Evolution GO.

Rodam em todo runserver/manage. Sem base_url ou token da instância o GO não responde, então travam o
manage.py até a env ser preenchida (CONVENTION §8).

- `whatsapp.E001` (Error): sem WHATSAPP_API_KEY → TRAVA.
- `whatsapp.E002` (Error): sem WHATSAPP_API_BASE_URL → TRAVA.
"""

from django.conf import settings
from django.core.checks import Error


def check_whatsapp_env(app_configs, **kwargs):
    errors = []
    if not getattr(settings, "WHATSAPP_API_KEY", ""):
        errors.append(
            Error(
                "WHATSAPP_API_KEY ausente — o app não autentica na instância do Evolution GO.",
                hint="Defina o token da instância em WHATSAPP_API_KEY.",
                id="whatsapp.E001",
            )
        )
    if not getattr(settings, "WHATSAPP_API_BASE_URL", ""):
        errors.append(
            Error(
                "WHATSAPP_API_BASE_URL ausente — sem a URL do Evolution GO o app não envia nada.",
                hint="Defina WHATSAPP_API_BASE_URL=http://host:porta.",
                id="whatsapp.E002",
            )
        )
    return errors
