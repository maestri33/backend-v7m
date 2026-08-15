from django.apps import AppConfig


class NotifyIntegrationConfig(AppConfig):
    name = "integrations.notify"
    label = "notify"
    verbose_name = "Integração com notify-server"

    def ready(self):
        from django.core.checks import register

        from integrations.notify.checks import check_notify_env

        register(check_notify_env)
