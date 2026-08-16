import json

import httpx
from asgiref.sync import async_to_sync
from django.test import SimpleTestCase, override_settings

from .client import WhatsAppClient, WhatsAppError


@override_settings(
    WHATSAPP_API_BASE_URL="http://evolution-go.test:4000",
    WHATSAPP_API_KEY="token-de-teste",
)
class WhatsAppClientTests(SimpleTestCase):
    def run_async(self, coroutine):
        async def runner():
            return await coroutine

        return async_to_sync(runner)()

    def test_health_usa_status_da_instancia_e_token(self):
        async def handler(request):
            self.assertEqual(request.method, "GET")
            self.assertEqual(request.url.path, "/instance/status")
            self.assertEqual(request.headers["apikey"], "token-de-teste")
            return httpx.Response(
                200,
                json={"data": {"Connected": True, "LoggedIn": True}},
            )

        async def run():
            async with WhatsAppClient(
                transport=httpx.MockTransport(handler)
            ) as client:
                return await client.health()

        result = self.run_async(run())
        self.assertTrue(result["data"]["Connected"])

    def test_check_numbers_normaliza_resposta_do_go(self):
        async def handler(request):
            self.assertEqual(request.url.path, "/user/check")
            self.assertEqual(
                json.loads(request.content),
                {"number": ["5543999999999", "554388888888"]},
            )
            return httpx.Response(
                200,
                json={
                    "data": {
                        "Users": [
                            {
                                "Query": "5543999999999",
                                "IsInWhatsapp": True,
                                "JID": "5543999999999@s.whatsapp.net",
                                "RemoteJID": "5543999999999@s.whatsapp.net",
                                "VerifiedName": "Pessoa",
                            },
                            {
                                "Query": "554388888888",
                                "IsInWhatsapp": False,
                                "JID": "",
                                "RemoteJID": "",
                                "VerifiedName": "",
                            },
                        ]
                    }
                },
            )

        async def run():
            async with WhatsAppClient(
                transport=httpx.MockTransport(handler)
            ) as client:
                return await client.check_numbers(
                    ["5543999999999", "554388888888"]
                )

        result = self.run_async(run())
        self.assertEqual(
            result,
            [
                {
                    "jid": "5543999999999@s.whatsapp.net",
                    "exists": True,
                    "number": "5543999999999",
                    "name": "Pessoa",
                },
                {
                    "jid": None,
                    "exists": False,
                    "number": "554388888888",
                    "name": None,
                },
            ],
        )

    def test_envio_de_texto_usa_contrato_go(self):
        async def handler(request):
            self.assertEqual(request.url.path, "/send/text")
            self.assertEqual(
                json.loads(request.content),
                {"number": "5543999999999", "text": "Olá", "delay": 250},
            )
            return httpx.Response(200, json={"data": {"id": "msg-1"}})

        async def run():
            async with WhatsAppClient(
                transport=httpx.MockTransport(handler)
            ) as client:
                return await client.send_text("5543999999999", "Olá", delay=250)

        result = self.run_async(run())
        self.assertEqual(result["data"]["id"], "msg-1")

    def test_check_numbers_aceita_variantes_consolidadas_pelo_go(self):
        async def handler(request):
            return httpx.Response(
                200,
                json={
                    "data": {
                        "Users": [
                            {
                                "Query": "5543999999999",
                                "IsInWhatsapp": True,
                                "JID": "5543999999999@s.whatsapp.net",
                                "RemoteJID": "5543999999999@s.whatsapp.net",
                                "VerifiedName": "",
                            }
                        ]
                    }
                },
            )

        async def run():
            async with WhatsAppClient(
                transport=httpx.MockTransport(handler)
            ) as client:
                return await client.check_numbers(
                    ["5543999999999", "554399999999"]
                )

        result = self.run_async(run())
        self.assertEqual(len(result), 1)
        self.assertTrue(result[0]["exists"])

    def test_envio_de_midia_usa_url_type_e_filename_do_go(self):
        async def handler(request):
            self.assertEqual(request.url.path, "/send/media")
            self.assertEqual(
                json.loads(request.content),
                {
                    "number": "5543999999999",
                    "url": "http://backend.test/media/arquivo.pdf",
                    "type": "document",
                    "caption": "Documento",
                    "filename": "arquivo.pdf",
                },
            )
            return httpx.Response(200, json={"data": {"id": "msg-2"}})

        async def run():
            async with WhatsAppClient(
                transport=httpx.MockTransport(handler)
            ) as client:
                return await client.send_media(
                    "5543999999999",
                    "http://backend.test/media/arquivo.pdf",
                    "document",
                    caption="Documento",
                    filename="arquivo.pdf",
                )

        result = self.run_async(run())
        self.assertEqual(result["data"]["id"], "msg-2")

    def test_audio_reusa_send_media_com_tipo_audio_ptt(self):
        async def handler(request):
            self.assertEqual(request.url.path, "/send/media")
            self.assertEqual(json.loads(request.content)["type"], "audio")
            return httpx.Response(200, json={"data": {"id": "voice-1"}})

        async def run():
            async with WhatsAppClient(
                transport=httpx.MockTransport(handler)
            ) as client:
                return await client.send_whatsapp_audio(
                    "5543999999999",
                    "http://backend.test/media/audio.mp3",
                )

        result = self.run_async(run())
        self.assertEqual(result["data"]["id"], "voice-1")

    def test_midia_sem_url_e_rejeitada_antes_da_rede(self):
        async def run():
            async with WhatsAppClient(
                transport=httpx.MockTransport(lambda request: None)
            ) as client:
                await client.send_media(
                    "5543999999999",
                    "Y29udGV1ZG8=",
                    "document",
                )

        with self.assertRaisesRegex(WhatsAppError, "URL http"):
            self.run_async(run())
