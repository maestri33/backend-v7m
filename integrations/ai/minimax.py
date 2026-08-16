"""Client MiniMax — TTS (t2a_v2) + visão (descrever imagem com MiniMax-M3), via REST (httpx, sem SDK).

API: base `https://api.minimax.io`, auth `Authorization: Bearer <key>`.
- TTS:    `POST /v1/t2a_v2` → corpo `{model, text, voice_setting, audio_setting}`; o áudio volta em
          `data.audio` como string **hexadecimal** (decodificar com `bytes.fromhex`).
- Visão:  `POST /v1/chat/completions` (OpenAI-compatible) com `MiniMax-M3` + a imagem inline
          (data-URL base64) + `thinking: disabled` (mata o bloco <think> do raciocínio) → texto.

Config (key/base_url/modelos/vozes) vem do .env via settings (CONVENTION §10). Zero regra de
negócio (§8): só fala com o provider e devolve o resultado cru. Consumido pelo `service.py` (mídia).
"""

from __future__ import annotations

import base64

import httpx
import structlog
from django.conf import settings

logger = structlog.get_logger()


class MiniMaxError(Exception):
    """Erro ao falar com o MiniMax (TTS ou visão)."""


class MiniMaxClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 90.0,
    ):
        self._api_key = api_key if api_key is not None else settings.MINIMAX_API_KEY
        self._base_url = (base_url or settings.MINIMAX_BASE_URL).rstrip("/")
        self._tts_model = settings.MINIMAX_TTS_MODEL
        self._vision_model = settings.MINIMAX_VISION_MODEL
        self._timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    async def tts(
        self,
        text: str,
        *,
        voice_id: str | None = None,
        model: str | None = None,
        output_format: str = "mp3",
    ) -> bytes:
        """Converte texto em fala (t2a_v2). Devolve os bytes do áudio (mp3)."""
        url = f"{self._base_url}/v1/t2a_v2"
        body = {
            "model": model or self._tts_model,
            "text": text,
            "stream": False,
            "voice_setting": {
                "voice_id": voice_id or settings.MINIMAX_VOICE_FEMALE,
                "speed": 1,
                "vol": 1,
                "pitch": 0,
            },
            "audio_setting": {
                "sample_rate": 32000,
                "bitrate": 128000,
                "format": output_format,
                "channel": 1,
            },
        }
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(self._timeout, connect=10.0)
        ) as c:
            resp = await c.post(url, json=body, headers=self._headers())
        if resp.status_code >= 400:
            raise MiniMaxError(
                f"MiniMax TTS HTTP {resp.status_code}: {resp.text[:300]}"
            )
        data = resp.json()
        base = data.get("base_resp") or {}
        if base.get("status_code") not in (0, None):
            raise MiniMaxError(
                f"MiniMax TTS status {base.get('status_code')}: {base.get('status_msg')}"
            )
        audio_hex = (data.get("data") or {}).get("audio")
        if not audio_hex:
            raise MiniMaxError("MiniMax TTS não retornou áudio")
        audio = bytes.fromhex(audio_hex)
        logger.info(
            "minimax.tts_done",
            model=body["model"],
            voice=body["voice_setting"]["voice_id"],
            text_len=len(text),
            audio_kb=round(len(audio) / 1024, 1),
        )
        return audio

    async def describe(
        self,
        image_bytes: bytes,
        *,
        mime_type: str = "image/jpeg",
        prompt: str | None = None,
    ) -> str:
        """Descreve/analisa uma imagem com MiniMax-M3 (visão). Devolve o texto, sem o bloco <think>."""
        instruction = (
            prompt
            or "Descreva esta imagem em portugues brasileiro de forma clara e objetiva."
        )
        data_url = f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode()}"
        body = {
            "model": self._vision_model,
            "thinking": {"type": "disabled"},  # sem o bloco <think> de raciocínio
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": instruction},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
            ],
            "max_completion_tokens": 800,
        }
        url = f"{self._base_url}/v1/chat/completions"
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(self._timeout, connect=10.0)
        ) as c:
            resp = await c.post(url, json=body, headers=self._headers())
        if resp.status_code >= 400:
            raise MiniMaxError(
                f"MiniMax visão HTTP {resp.status_code}: {resp.text[:300]}"
            )
        data = resp.json()
        choices = data.get("choices") or []
        if not choices:
            base = data.get("base_resp") or {}
            raise MiniMaxError(
                f"MiniMax visão sem resposta (status {base.get('status_code')}: "
                f"{base.get('status_msg')})"
            )
        text = (choices[0].get("message") or {}).get("content") or ""
        logger.info(
            "minimax.vision_done",
            model=self._vision_model,
            bytes=len(image_bytes),
            chars=len(text),
        )
        return text.strip()

    async def generate_image(
        self,
        prompt: str,
        *,
        subject_image: bytes | None = None,
        subject_mime: str = "image/jpeg",
        aspect_ratio: str = "3:4",
        model: str = "image-01",
    ) -> bytes:
        """Gera uma imagem (image-01). Com `subject_image` usa `subject_reference` (consistência de
        rosto/personagem — preserva a cara). Devolve os bytes da 1ª imagem."""
        body: dict = {
            "model": model,
            "prompt": prompt,
            "aspect_ratio": aspect_ratio,
            "response_format": "base64",
            "n": 1,
            "prompt_optimizer": True,
        }
        if subject_image is not None:
            data_url = (
                f"data:{subject_mime};base64,{base64.b64encode(subject_image).decode()}"
            )
            body["subject_reference"] = [{"type": "character", "image_file": data_url}]
        url = f"{self._base_url}/v1/image_generation"
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(self._timeout, connect=10.0)
        ) as c:
            resp = await c.post(url, json=body, headers=self._headers())
        if resp.status_code >= 400:
            raise MiniMaxError(
                f"MiniMax imagem HTTP {resp.status_code}: {resp.text[:300]}"
            )
        data = resp.json()
        base = data.get("base_resp") or {}
        if base.get("status_code") not in (0, None):
            raise MiniMaxError(
                f"MiniMax imagem status {base.get('status_code')}: {base.get('status_msg')}"
            )
        d = data.get("data") or {}
        b64list = d.get("image_base64") or []
        if b64list:
            img_bytes = base64.b64decode(b64list[0])
        else:
            urls = d.get("image_urls") or []
            if not urls:
                raise MiniMaxError(f"MiniMax imagem sem retorno: {resp.text[:200]}")
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout, connect=10.0)
            ) as c:
                got = await c.get(urls[0])
            img_bytes = got.content
        logger.info(
            "minimax.image_done",
            model=model,
            kb=round(len(img_bytes) / 1024, 1),
            subject=bool(subject_image),
        )
        return img_bytes
