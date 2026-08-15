from django.core.management.base import BaseCommand

from notifications.seed import seed_notification_templates


class Command(BaseCommand):
    help = "Cria eventos fixos e atualiza apenas conteúdos não personalizados."

    def handle(self, *args, **options):
        result = seed_notification_templates()
        self.stdout.write(
            self.style.SUCCESS(
                "notificações: "
                f"{result['created']} criadas, {result['updated']} atualizadas, "
                f"{result['preserved']} personalizadas preservadas"
            )
        )
