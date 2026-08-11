"""Validação manual de ENVIO do notify (§8): dispara uma notificação REAL multi-canal.

Uso:
  python manage.py notify_send --phone 5543996648750 --title "Teste" --text "olá do notify"
  python manage.py notify_send --phone 5543... --email a@b.com --email-channel --tts --text "..."
  # com imagem (WhatsApp pela LAN, e-mail pela URL pública):
  python manage.py notify_send --phone 55... --email a@b.com --whatsapp --email-channel \
      --text "Seu QR" --media-url https://dev.m33.live/media/qrcodes/pay_x.png --media-type image

Roda o despacho inline (`run_sync`) p/ ver o resultado real na hora. Imprime o registro com o
status de cada canal. Sem flag de canal explícita, liga o whatsapp por default.
"""

import json

from django.core.management.base import BaseCommand

from notify.interface.send import send


class Command(BaseCommand):
    help = (
        "Dispara uma notificação real (whatsapp/email/tts) e mostra o status por canal."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--phone", default=None, help="Destinatário whatsapp/tts (DDI+DDD+nº)"
        )
        parser.add_argument("--email", default=None, help="Destinatário do e-mail")
        parser.add_argument(
            "--title", default=None, help="Título (negrito no whatsapp / subject)"
        )
        parser.add_argument(
            "--subject", default=None, help="Assunto do e-mail (fallback: título)"
        )
        parser.add_argument("--text", required=True, help="Corpo da mensagem")
        parser.add_argument(
            "--mail-template", default="default", help="Slug do template do mail"
        )
        parser.add_argument(
            "--media-url", default=None, help="URL pública da mídia (imagem etc.)"
        )
        parser.add_argument(
            "--media-type",
            default=None,
            help="image/video/audio/document (auto-detect pela extensão se omitido)",
        )
        parser.add_argument(
            "--gender", default=None, help="M/F — voz do TTS (default: voz padrão)"
        )
        parser.add_argument(
            "--whatsapp", action="store_true", help="ligar canal whatsapp"
        )
        parser.add_argument(
            "--email-channel", action="store_true", help="ligar canal e-mail"
        )
        parser.add_argument("--tts", action="store_true", help="ligar voice-note (TTS)")
        parser.add_argument(
            "--caller", default="notify_send", help="quem emitiu (auditoria)"
        )

    def handle(self, *args, **o):
        # nenhum canal explícito => liga whatsapp por default.
        any_channel = o["whatsapp"] or o["email_channel"] or o["tts"]
        whatsapp = o["whatsapp"] or not any_channel

        external_id = send(
            text=o["text"],
            caller=o["caller"],
            phone=o["phone"],
            email=o["email"],
            title=o["title"],
            subject=o["subject"],
            whatsapp=whatsapp,
            email_channel=o["email_channel"],
            tts=o["tts"],
            media_url=o["media_url"],
            media_type=o["media_type"],
            gender=o["gender"],
            mail_template=o["mail_template"],
            run_sync=True,
        )

        # run_sync=True já despachou no notify-server — a row vive LÁ, não no ORM local. Consulta o SDK.
        from notify.sdk import client as sdk

        remote = sdk.get_notification(external_id)
        if remote is None:
            self.stdout.write(
                self.style.WARNING(
                    f"Notification {external_id}: despachada, mas o notify-server "
                    "ainda não a encontra (tente de novo em instantes)."
                )
            )
            return
        self.stdout.write(self.style.SUCCESS(f"Notification {external_id} (remote):"))
        self.stdout.write(json.dumps(remote, ensure_ascii=False, indent=2))
