from django.conf import settings
from django.core.checks import Error


def check_notify_env(app_configs, **kwargs):
    if getattr(settings, "NOTIFY_SERVER_URL", "") and getattr(settings, "NOTIFY_API_KEY", ""):
        return []
    return [
        Error(
            "NOTIFY_SERVER_URL e NOTIFY_API_KEY são obrigatórios.",
            hint="Configure o serviço independente notify-server no .env.",
            id="notify.E001",
        )
    ]
