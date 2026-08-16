"""Cliente enxuto para Evolution GO (WhatsApp).

Contrato alvo:
- autenticação da instância pelo header ``apikey``;
- ``GET /instance/status``;
- ``POST /user/check``;
- ``POST /send/text``;
- ``POST /send/media``.

A instância não vai na URL: o Evolution GO a identifica pelo token. O cliente mantém somente a
superfície usada pelo auth, pelo notify e pelos comandos de validação do projeto.
"""

from __future__ import annotations

import time
from typing import Any

import httpx
import structlog
from django.conf import settings

logger = structlog.get_logger()

MEDIA_TYPES = {"image", "video", "audio", "document"}
_BR_JID_TTL_S = 3600
_br_jid_cache: dict[str, tuple[str | None, float]] = {}


def _br_phone_variants(phone: str) -> list[str]:
    """Gera as variantes brasileiras com e sem o nono dígito."""
    digits = "".join(c for c in phone if c.isdigit())
    if not digits.startswith("55") or len(digits) not in (12, 13):
        return [digits or phone]

    country, ddd, rest = digits[:2], digits[2:4], digits[4:]
    if len(rest) == 9 and rest.startswith("9"):
        return [country + ddd + rest, country + ddd + rest[1:]]
    if len(rest) == 8:
        return [country + ddd + "9" + rest, country + ddd + rest]
    return [digits]


class WhatsAppError(Exception):
    """Evolution GO respondeu erro ou um contrato inesperado."""

    def __init__(self, status_code: int, body: Any, message: str = ""):
        self.status_code = status_code
        self.body = body
        super().__init__(message or f"Evolution GO {status_code}: {body!r}")


class WhatsAppClient:
    """Cliente dos recursos do Evolution GO usados pelo projeto."""

    def __init__(
        self,
        *,
        timeout: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=settings.WHATSAPP_API_BASE_URL.rstrip("/"),
            headers={"apikey": settings.WHATSAPP_API_KEY},
            timeout=timeout,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Any:
        kwargs: dict[str, Any] = {"json": json} if json is not None else {}
        if timeout is not None:
            kwargs["timeout"] = httpx.Timeout(timeout, connect=5.0)

        response = await self._client.request(method, path, **kwargs)
        if response.status_code >= 400:
            raise WhatsAppError(response.status_code, response.text)
        try:
            return response.json()
        except ValueError as exc:
            raise WhatsAppError(
                response.status_code,
                response.text,
                "Evolution GO respondeu conteúdo que não é JSON",
            ) from exc

    async def health(self) -> Any:
        """Confirma autenticação, conexão e login da instância do token."""
        return await self._request("GET", "/instance/status")

    async def check_numbers(self, numbers: list[str]) -> list[dict[str, Any]]:
        """Verifica números e normaliza a resposta GO para o contrato interno legado.

        Evolution GO responde ``data.Users`` com ``Query``, ``IsInWhatsapp``, ``JID``,
        ``RemoteJID`` e ``VerifiedName``. O restante do projeto consome ``exists``, ``jid``,
        ``number`` e ``name``.
        """
        result = await self._request("POST", "/user/check", json={"number": numbers})
        users = result.get("data", {}).get("Users") if isinstance(result, dict) else None
        if not isinstance(users, list):
            raise WhatsAppError(
                200,
                result,
                "Evolution GO devolveu resposta inesperada em /user/check",
            )

        normalized = []
        # O whatsmeow pode consolidar duas variantes do mesmo telefone em uma única resposta.
        for index, user in enumerate(users):
            requested = user.get("Query") or (
                numbers[index] if index < len(numbers) else ""
            )
            jid = user.get("JID") or None
            remote_jid = user.get("RemoteJID") or jid
            normalized.append(
                {
                    "jid": jid,
                    "exists": bool(user.get("IsInWhatsapp")),
                    "number": remote_jid.split("@", 1)[0] if remote_jid else requested,
                    "name": user.get("VerifiedName") or None,
                }
            )

        logger.info("whatsapp.check", count=len(numbers), provider="evolution_go")
        return normalized

    async def resolve_br_number(self, phone: str) -> str:
        """Resolve a variante brasileira registrada, com cache de uma hora."""
        cached = _br_jid_cache.get(phone)
        if cached is not None:
            value, timestamp = cached
            if time.monotonic() - timestamp < _BR_JID_TTL_S:
                return value or phone
            del _br_jid_cache[phone]

        variants = _br_phone_variants(phone)
        if len(variants) == 1:
            return variants[0]

        try:
            result = await self.check_numbers(variants)
        except Exception as exc:
            logger.warning(
                "whatsapp.resolve_br.check_failed",
                error=f"{type(exc).__name__}: {exc!r}",
            )
            return phone

        chosen = next((item["number"] for item in result if item["exists"]), None)
        _br_jid_cache[phone] = (chosen, time.monotonic())
        if chosen is None:
            logger.warning("whatsapp.resolve_br.none_exists", variant_count=len(variants))
            return phone
        if chosen != phone:
            logger.info(
                "whatsapp.resolve_br.normalized",
                changed=True,
            )
        return chosen

    async def send_text(
        self,
        number: str,
        text: str,
        *,
        delay: int | None = None,
        format_jid: bool | None = None,
    ) -> dict[str, Any]:
        """Envia texto por ``POST /send/text``."""
        payload: dict[str, Any] = {"number": number, "text": text}
        if delay is not None:
            payload["delay"] = delay
        if format_jid is not None:
            payload["formatJid"] = format_jid

        result = await self._request("POST", "/send/text", json=payload)
        logger.info("whatsapp.text_sent", provider="evolution_go")
        return result

    async def send_media(
        self,
        number: str,
        media_url: str,
        media_type: str,
        *,
        caption: str | None = None,
        filename: str | None = None,
        delay: int | None = None,
        format_jid: bool | None = None,
    ) -> dict[str, Any]:
        """Envia mídia por URL em ``POST /send/media``.

        No GO, ``type=audio`` converte o arquivo para Opus e envia como PTT. O endpoint não aceita o
        payload/base64 da Evolution API v2; por isso ``media_url`` deve ser uma URL alcançável pelo
        servidor GO.
        """
        if media_type not in MEDIA_TYPES:
            raise WhatsAppError(
                0,
                media_type,
                f"media_type inválido: {media_type}. Use: {', '.join(sorted(MEDIA_TYPES))}",
            )
        if not media_url.startswith(("http://", "https://")):
            raise WhatsAppError(
                0,
                "<mídia omitida>",
                "Evolution GO requer uma URL http(s) em send_media",
            )

        payload: dict[str, Any] = {
            "number": number,
            "url": media_url,
            "type": media_type,
        }
        if caption:
            payload["caption"] = caption
        if filename:
            payload["filename"] = filename
        if delay is not None:
            payload["delay"] = delay
        if format_jid is not None:
            payload["formatJid"] = format_jid

        result = await self._request("POST", "/send/media", json=payload, timeout=60.0)
        logger.info(
            "whatsapp.media_sent",
            type=media_type,
            provider="evolution_go",
        )
        return result

    async def send_whatsapp_audio(self, number: str, audio_url: str) -> dict[str, Any]:
        """Envia nota de voz: no Evolution GO, ``type=audio`` já produz PTT."""
        return await self.send_media(number, audio_url, "audio")


def get_client() -> WhatsAppClient:
    """Constrói o cliente usando somente configuração externa."""
    return WhatsAppClient()
