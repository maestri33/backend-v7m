from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from notify.dispatch import _send_tts, _send_whatsapp
from notify.models import STATUS_SENT


class FakeWhatsAppClient:
    def __init__(self):
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def resolve_br_number(self, number):
        return number

    async def send_text(self, number, text):
        self.calls.append(("text", number, text))

    async def send_media(self, number, url, media_type, *, caption):
        self.calls.append(("media", number, url, media_type, caption))

    async def send_whatsapp_audio(self, number, url):
        self.calls.append(("audio", number, url))


def notification(**overrides):
    values = {
        "external_id": "teste",
        "recipient_phone": "5543999999999",
        "title": "Título",
        "text": "Mensagem",
        "media_url": None,
        "media_type": None,
        "gender": None,
        "whatsapp_status": "pending",
        "whatsapp_error": None,
        "tts_status": "pending",
        "tts_error": None,
        "tts_audio_path": None,
        "caller": "teste",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@override_settings(
    MEDIA_LAN_BASE="http://backend.lan",
    EXTERNAL_URL="https://backend.example",
    MEDIA_URL="/media/",
)
class NotifyEvolutionGoTests(SimpleTestCase):
    def test_texto_e_enviado_pelo_cliente_go(self):
        client = FakeWhatsAppClient()
        notif = notification()

        with patch("notify.dispatch.get_whatsapp_client", return_value=client):
            _send_whatsapp(notif)

        self.assertEqual(notif.whatsapp_status, STATUS_SENT)
        self.assertEqual(
            client.calls,
            [("text", "5543999999999", "*Título*\n\nMensagem")],
        )

    def test_midia_e_reescrita_para_url_alcancavel_pelo_go(self):
        client = FakeWhatsAppClient()
        notif = notification(
            media_url="https://backend.example/media/imagem.png",
            media_type="image",
        )

        with patch("notify.dispatch.get_whatsapp_client", return_value=client):
            _send_whatsapp(notif)

        self.assertEqual(notif.whatsapp_status, STATUS_SENT)
        self.assertEqual(
            client.calls,
            [
                (
                    "media",
                    "5543999999999",
                    "http://backend.lan/media/imagem.png",
                    "image",
                    "*Título*\n\nMensagem",
                )
            ],
        )

    def test_tts_vai_para_audio_ptt_do_go(self):
        client = FakeWhatsAppClient()
        notif = notification()

        with (
            patch("notify.dispatch.get_whatsapp_client", return_value=client),
            patch("notify.dispatch.ai_service.tts", return_value="ai/audio/teste.mp3"),
        ):
            _send_tts(notif)

        self.assertEqual(notif.tts_status, STATUS_SENT)
        self.assertEqual(notif.tts_audio_path, "ai/audio/teste.mp3")
        self.assertEqual(
            client.calls,
            [
                (
                    "audio",
                    "5543999999999",
                    "http://backend.lan/media/ai/audio/teste.mp3",
                )
            ],
        )
