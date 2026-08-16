"""Validação manual de ENVIO DE MÍDIA: manda imagem/vídeo/áudio/documento real (§8).

Uso:
  python manage.py whatsapp_send_media 5543996648750 image https://picsum.photos/400
  python manage.py whatsapp_send_media 5543996648750 document https://.../arquivo.pdf \
      --filename matricula.pdf
  python manage.py whatsapp_send_media 5543996648750 audio https://.../som.mp3  # PTT

O Evolution GO busca a mídia por URL e envia áudio como nota de voz (PTT).
"""

import json

from asgiref.sync import async_to_sync
from django.core.management.base import BaseCommand, CommandError

from integrations.communication.whatsapp.client import (
    MEDIA_TYPES,
    WhatsAppError,
    get_client,
)


class Command(BaseCommand):
    help = "Envia mídia por URL via Evolution GO (áudio vira nota de voz PTT)."

    def add_arguments(self, parser):
        parser.add_argument(
            "number", help="Destinatário DDI+DDD+número (ex.: 5543996648750)"
        )
        parser.add_argument(
            "media_type", choices=sorted(MEDIA_TYPES), help="image|video|audio|document"
        )
        parser.add_argument(
            "source", help="URL http(s) alcançável pelo Evolution GO"
        )
        parser.add_argument(
            "--caption", default=None, help="Legenda (image/video/document)"
        )
        parser.add_argument(
            "--filename", default=None, help="Nome do arquivo (document)"
        )
    def _resolve_source(self, source: str) -> str:
        """O GO aceita URL; upload/base64 pertencia ao contrato antigo."""
        if source.startswith(("http://", "https://")):
            return source
        raise CommandError("source deve ser uma URL http(s) alcançável pelo Evolution GO")

    def handle(self, *args, **options):
        number = options["number"]
        media_type = options["media_type"]
        media = self._resolve_source(options["source"])

        async def _run():
            async with get_client() as wa:
                resolved = await wa.resolve_br_number(number)
                result = await wa.send_media(
                    resolved,
                    media,
                    media_type,
                    caption=options["caption"],
                    filename=options["filename"],
                )
                return resolved, result

        try:
            resolved, result = async_to_sync(_run)()
        except WhatsAppError as exc:
            self.stderr.write(self.style.ERROR(f"Evolution respondeu erro: {exc}"))
            return
        except Exception as exc:
            self.stderr.write(self.style.ERROR(f"Falha ao enviar: {exc!r}"))
            return

        kind = "nota de voz (PTT)" if media_type == "audio" else media_type
        self.stdout.write(self.style.SUCCESS(f"Enviado [{kind}] para {resolved}:"))
        self.stdout.write(json.dumps(result, ensure_ascii=False, indent=2))
