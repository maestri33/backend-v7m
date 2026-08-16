"""Validação do Evolution GO: consulta a instância do token. NÃO envia mensagem.

Uso: python manage.py whatsapp_health
Fecha parte do Portão 3 (§8 — integração validada com chamada real).
"""

import json

from asgiref.sync import async_to_sync
from django.core.management.base import BaseCommand

from integrations.communication.whatsapp.client import WhatsAppError, get_client


class Command(BaseCommand):
    help = "Consulta o status da instância do Evolution GO (auth + conectividade)."

    def handle(self, *args, **options):
        async def _run():
            async with get_client() as wa:
                return await wa.health()

        try:
            result = async_to_sync(_run)()
        except WhatsAppError as exc:
            self.stderr.write(self.style.ERROR(f"Evolution respondeu erro: {exc}"))
            return
        except Exception as exc:  # rede/DNS/timeout
            self.stderr.write(
                self.style.ERROR(f"Falha ao falar com a Evolution: {exc!r}")
            )
            return

        self.stdout.write(self.style.SUCCESS("Evolution GO respondeu (auth ok):"))
        self.stdout.write(json.dumps(result, ensure_ascii=False, indent=2))
