from django.apps import AppConfig


class NotificationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "notifications"
    verbose_name = "Conteúdo das notificações"

    def ready(self):
        from django.db.models.signals import post_migrate

        from notifications.seed import seed_after_migrate

        post_migrate.connect(
            seed_after_migrate,
            sender=self,
            dispatch_uid="notifications.seed_after_migrate",
        )
