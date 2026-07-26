"""Cliente para Evolution API 2.3.7 (WhatsApp) — porte do micro legado (notify/whats).

API alvo: settings.WHATSAPP_API_BASE_URL. Auth: header `apikey`
(settings.WHATSAPP_GLOBAL_API_KEY). Instância default em settings.WHATSAPP_INSTANCE_NAME,
sobreponível no construtor (ex.: "ieadpg").

Regras (CONVENTION §8/§10):
 - base_url/api-key/instance vêm do .env via settings. Zero regra de negócio aqui.
 - httpx puro (sem retry): qualquer não-2xx vira WhatsAppError; quem chama (notify, async) decide.
 - resolve_br_number() resolve a variante BR com/sem o 9º dígito (evita silent-fail da Evolution).

Endpoints: health · check_numbers · resolve_br_number · send_text · send_media · send_whatsapp_audio.
"""

from __future__ import annotations

import time
from typing import Any

import httpx
import structlog
from django.conf import settings

logger = structlog.get_logger()

MEDIA_TYPES = {"image", "video", "audio", "document"}

# ── Cache de resolução de JID BR (nono dígito) ─────────────────────────────
# Mapeia phone-original -> (resolved_number, monotonic_ts). TTL 1h. Evita pagar 1 HTTP extra a
# cada send pro mesmo contato.
_BR_JID_TTL_S = 3600
_br_jid_cache: dict[str, tuple[str | None, float]] = {}


def _br_phone_variants(phone: str) -> list[str]:
    """Para mobile BR, gera as duas variantes (com 9 / sem 9).

    Mobile BR moderno: `55` + DDD(2) + `9` + 8 dígitos = 13 dígitos. Legado: `55` + DDD(2) +
    8 dígitos = 12. Alguns números só estão registrados no WhatsApp em UMA das formas, o que faz
    o `formatJid` automático da Evolution errar a normalização. Para não-BR/non-mobile, retorna
    apenas [phone].
    """
    digits = "".join(c for c in phone if c.isdigit())
    if not digits.startswith("55") or len(digits) not in (12, 13):
        return [phone]
    country, ddd, rest = digits[:2], digits[2:4], digits[4:]
    if len(rest) == 9 and rest.startswith("9"):
        return [country + ddd + rest, country + ddd + rest[1:]]  # com 9, sem 9
    if len(rest) == 8:
        return [country + ddd + "9" + rest, country + ddd + rest]  # com 9, sem 9
    return [phone]


class WhatsAppError(Exception):
    """Evolution API respondeu não-2xx. Quem chama decide o que fazer."""

    def __init__(self, status_code: int, body: Any, message: str = ""):
        self.status_code = status_code
        self.body = body
        super().__init__(message or f"WhatsApp API {status_code}: {body!r}")


class WhatsAppClient:
    """Cliente de alto nível para a Evolution API v2 (WhatsApp)."""

    # default 10s: check_numbers roda DENTRO do request do register/check — timeout alto = stall do
    # serviço inteiro (auditoria do front 2026-06-10). Chamada de worker que precisa de mais passa
    # timeout explícito (ex.: áudio 60s).
    def __init__(self, *, instance: str | None = None, timeout: float = 10.0) -> None:
        self._client = httpx.AsyncClient(
            base_url=settings.WHATSAPP_API_BASE_URL,
            headers={"apikey": settings.WHATSAPP_GLOBAL_API_KEY},
            timeout=timeout,
        )
        self._instance = instance or settings.WHATSAPP_INSTANCE_NAME

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        await self.aclose()

    # ---------- helpers ----------
    def _msg_path(self, endpoint: str) -> str:
        """POST /message/<endpoint>/<instance>"""
        return f"/message/{endpoint}/{self._instance}"

    def _chat_path(self, endpoint: str) -> str:
        """POST /chat/<endpoint>/<instance>"""
        return f"/chat/{endpoint}/{self._instance}"

    async def _post(
        self, path: str, json: dict[str, Any], *, timeout: float | None = None
    ) -> Any:
        kwargs: dict[str, Any] = {}
        if timeout is not None:
            kwargs["timeout"] = httpx.Timeout(timeout, connect=5.0)
        resp = await self._client.post(path, json=json, **kwargs)
        if resp.status_code >= 400:
            raise WhatsAppError(resp.status_code, resp.text)
        return resp.json()

    async def _get(self, path: str) -> Any:
        resp = await self._client.get(path)
        if resp.status_code >= 400:
            raise WhatsAppError(resp.status_code, resp.text)
        return resp.json()

    def _build_quoted(
        self, message_id: str | None, participant: str | None = None
    ) -> dict[str, str] | None:
        """Monta o objeto quoted (reply) se houver message_id."""
        if not message_id:
            return None
        quoted: dict[str, str] = {"messageId": message_id}
        if participant:
            quoted["participant"] = participant
        return quoted

    # ---------- health ----------
    async def health(self) -> Any:
        """Status global da Evolution. v2.3.7 não tem /instance/status — usa fetchInstances."""
        return await self._get("/instance/fetchInstances")

    # ---------- chat / user ----------
    async def check_numbers(self, numbers: list[str]) -> list[dict[str, Any]]:
        """Verifica se números possuem WhatsApp. POST /chat/whatsappNumbers/{instance}.

        numbers: formato DDI+DDD+número (ex.: "5543996648750"). Resposta: lista de
        {jid, exists, number, name}.
        """
        result = await self._post(
            self._chat_path("whatsappNumbers"), {"numbers": numbers}
        )
        logger.info("whatsapp.check", count=len(numbers))
        return result

    async def fetch_profile_picture(self, number: str) -> str | None:
        """URL da foto de perfil do número. POST /chat/fetchProfilePictureUrl/{instance}.

        Foto pública do WhatsApp (funil v2: pergaminho da tela 3-4). Sem foto/privada →
        a Evolution devolve profilePictureUrl nulo → None. A URL do CDN expira — quem
        guarda trata como enfeite perecível, nunca como dado durável."""
        result = await self._post(
            self._chat_path("fetchProfilePictureUrl"), {"number": number}
        )
        url = (result or {}).get("profilePictureUrl")
        logger.info("whatsapp.avatar_fetched", number=number, found=bool(url))
        return url if isinstance(url, str) and url else None

    async def resolve_br_number(self, phone: str) -> str:
        """Resolve qual variante BR (com/sem 9º dígito) está no WhatsApp.

        Retorna o `number` efetivamente registrado, ou o `phone` original como fallback. Cache em
        memória (TTL 1h) por `phone`. Pré-resolver evita silent-fail (Evolution responde 201 sem
        entregar quando o número só existe na outra variante).
        """
        cached = _br_jid_cache.get(phone)
        if cached is not None:
            value, ts = cached
            if time.monotonic() - ts < _BR_JID_TTL_S:
                return value or phone
            del _br_jid_cache[phone]

        variants = _br_phone_variants(phone)
        if len(variants) == 1:
            return phone  # não é BR mobile — sem variantes a testar.

        try:
            result = await self.check_numbers(variants)
        except Exception as exc:
            logger.warning(
                "whatsapp.resolve_br.check_failed",
                phone=phone,
                error=f"{type(exc).__name__}: {exc!r}",
            )
            return phone  # fallback: tenta com o original

        chosen: str | None = None
        for item in result or []:
            if item.get("exists"):
                chosen = item.get("number") or item.get("jid", "").split("@")[0]
                break

        _br_jid_cache[phone] = (chosen, time.monotonic())

        if chosen is None:
            logger.warning(
                "whatsapp.resolve_br.none_exists", phone=phone, tried=variants
            )
            return phone  # fallback
        if chosen != phone:
            logger.info(
                "whatsapp.resolve_br.normalized",
                phone_original=phone,
                phone_resolved=chosen,
            )
        return chosen

    # ---------- send text ----------
    async def send_text(
        self,
        number: str,
        text: str,
        *,
        delay: int | None = None,
        quoted_msg_id: str | None = None,
        quoted_participant: str | None = None,
        mention_all: bool = False,
        mentioned_jid: list[str] | None = None,
        format_jid: bool | None = None,
    ) -> dict[str, Any]:
        """Envia mensagem de texto. POST /message/sendText/{instance}.

        delay: ms de "digitando...". quoted_*: reply. mention_all/mentioned_jid: menções (grupos).
        format_jid=False pula a validação/formatação do número.
        """
        payload: dict[str, Any] = {"number": number, "text": text}
        if delay is not None:
            payload["delay"] = delay
        if quoted := self._build_quoted(quoted_msg_id, quoted_participant):
            payload["quoted"] = quoted
        if mention_all:
            payload["mentionAll"] = True
        if mentioned_jid:
            payload["mentionedJid"] = mentioned_jid
        if format_jid is not None:
            payload["formatJid"] = format_jid

        result = await self._post(self._msg_path("sendText"), payload)
        logger.info("whatsapp.text_sent", number=number, text_preview=text[:50])
        return result

    # ---------- send media ----------
    async def send_media(
        self,
        number: str,
        media_url: str,
        media_type: str,
        *,
        caption: str | None = None,
        filename: str | None = None,
        mimetype: str | None = None,
        delay: int | None = None,
        quoted_msg_id: str | None = None,
        quoted_participant: str | None = None,
        mention_all: bool = False,
        mentioned_jid: list[str] | None = None,
        format_jid: bool | None = None,
    ) -> dict[str, Any]:
        """Envia mídia. POST /message/sendMedia/{instance}.

        media_type ∈ {image, video, audio, document}. media_url: URL pública ou base64 PURO
        (sem prefixo). filename obrigatório p/ document. caption: legenda (image/video/document).
        """
        if media_type not in MEDIA_TYPES:
            raise WhatsAppError(
                0,
                media_type,
                f"media_type inválido: {media_type}. Use: {', '.join(sorted(MEDIA_TYPES))}",
            )

        payload: dict[str, Any] = {
            "number": number,
            "mediatype": media_type,
            "media": media_url,
        }
        if caption:
            payload["caption"] = caption
        if filename:
            payload["fileName"] = filename
        if mimetype:
            payload["mimetype"] = mimetype
        if delay is not None:
            payload["delay"] = delay
        if quoted := self._build_quoted(quoted_msg_id, quoted_participant):
            payload["quoted"] = quoted
        if mention_all:
            payload["mentionAll"] = True
        if mentioned_jid:
            payload["mentionedJid"] = mentioned_jid
        if format_jid is not None:
            payload["formatJid"] = format_jid

        result = await self._post(self._msg_path("sendMedia"), payload)
        logger.info(
            "whatsapp.media_sent", number=number, type=media_type, url=media_url[:80]
        )
        return result

    # ---------- send whatsapp audio (nota de voz nativa / PTT) ----------
    async def send_whatsapp_audio(
        self,
        number: str,
        audio_url: str,
        *,
        delay: int | None = None,
        quoted_msg_id: str | None = None,
        quoted_participant: str | None = None,
    ) -> dict[str, Any]:
        """Áudio como nota de voz nativa (PTT). POST /message/sendWhatsAppAudio/{instance}.

        audio_url: URL pública (mp3/wav/ogg...). Força UI de áudio (waveform), diferente do
        send_media(mediatype="audio").
        """
        payload: dict[str, Any] = {"number": number, "audio": audio_url}
        if delay is not None:
            payload["delay"] = delay
        if quoted := self._build_quoted(quoted_msg_id, quoted_participant):
            payload["quoted"] = quoted

        result = await self._post(
            self._msg_path("sendWhatsAppAudio"), payload, timeout=60.0
        )
        logger.info("whatsapp.audio_sent", number=number, url=audio_url[:80])
        return result


def get_client(*, instance: str | None = None) -> WhatsAppClient:
    """Constrói o client com base_url/api-key/instance do .env (config via settings — §10)."""
    return WhatsAppClient(instance=instance)
