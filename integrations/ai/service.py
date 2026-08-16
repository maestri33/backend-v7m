"""Interface in-process do app `ia` — a única superfície pública (CONVENTION §3).

Os outros apps do monólito chamam ESTAS funções (nunca o client direto). Cada função:
 1. caminha a **cadeia de fallback** `(provider, model)` (providers.fallback_chain) — em falha
    retryável (rede/timeout/429/5xx) cai pro próximo; em erro de input/contrato (4xx) para e erra;
 2. embrulha o client async em `async_to_sync` (Portão 2: interface SÍNCRONA, casa com django-q);
 3. **grava 1 `AiCall` por tentativa** (provider+model+status+tokens+latência) — auditoria/custo;
 4. devolve o resultado já no formato útil (str/dict/Grading).

`grade()` mora aqui por decisão do Victor (correção do training: nota 0–10 + justificativa, ≥6 ok).
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass

import structlog
from asgiref.sync import async_to_sync
from django.conf import settings

from . import providers
from .client import ChatResult, LLMError
from .models import AiCall

logger = structlog.get_logger()

# Modelos de raciocínio (MiniMax-M3 etc.) prefixam um bloco <think>...</think> no texto; o conteúdo
# útil vem depois. Removemos o bloco antes de usar/parsear — robusto p/ qualquer modelo da cadeia.
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def _strip_think(text: str) -> str:
    """Remove blocos <think>...</think> (raciocínio) do texto e apara espaços nas pontas."""
    return _THINK_RE.sub("", text or "").strip()


# ---------------------------------------------------------------------------
# Contrato de correção do training (porte do legado training/integrations/ai.py).
# Invariante de negócio: "toda nota gravada tem justificativa".
# ---------------------------------------------------------------------------
_GRADE_SCHEMA = (
    "Objeto JSON com exatamente dois campos: `nota` (numero inteiro de 0 a 10) e "
    "`justificativa` (string em portugues explicando a nota com base no que o trainee "
    "escreveu vs o gabarito)."
)
_GRADE_INSTRUCTION = (
    "Voce e corretor de uma plataforma de treinamento. Compare a resposta do trainee com o "
    "gabarito da materia e atribua uma nota inteira de 0 a 10. 6 ou mais significa aprovado; "
    "menor que 6 significa reprovado. Seja justo, exigente com o conteudo mas tolerante com a "
    "forma. Responda APENAS o JSON pedido — sem texto fora dele."
)


@dataclass(frozen=True)
class Grading:
    """Resultado de uma correção: nota 0–10 + justificativa pt-br (≥6 = aprovado)."""

    grade: float
    justification: str


def _record(
    *,
    operation: str,
    provider: str,
    model: str,
    caller: str,
    result: ChatResult | None,
    error: Exception | None,
    started_at: float,
) -> None:
    """Grava uma linha AiCall com as métricas de UMA tentativa (cost null — CONVENTION §8)."""
    AiCall.objects.create(
        provider=provider,
        operation=operation,
        model=model,
        caller=caller,
        status=AiCall.Status.ERROR if error is not None else AiCall.Status.SUCCESS,
        prompt_tokens=getattr(result, "prompt_tokens", 0) or 0,
        completion_tokens=getattr(result, "completion_tokens", 0) or 0,
        cache_hit_tokens=getattr(result, "cache_hit_tokens", 0) or 0,
        cache_miss_tokens=getattr(result, "cache_miss_tokens", 0) or 0,
        cost=None,
        latency_ms=int((time.monotonic() - started_at) * 1000),
        finish_reason=getattr(result, "finish_reason", None),
        error=str(error)[:1000] if error is not None else None,
    )


def _run(operation: str, caller: str, attempt, chain) -> tuple[ChatResult, str, str]:
    """Caminha a cadeia: tenta cada (provider, model), grava AiCall por tentativa, para na 1ª que dá
    certo. `attempt` é uma corrotina `attempt(client, model) -> ChatResult`. Falha retryável => próximo;
    não-retryável (4xx/inesperada) => levanta na hora. Cadeia esgotada => levanta a última falha."""
    last_err: Exception | None = None
    for provider, model in chain:
        client = providers.get_client(provider)
        started = time.monotonic()
        try:
            result: ChatResult = async_to_sync(attempt)(client, model)
        except LLMError as exc:
            _record(
                operation=operation,
                provider=provider,
                model=model,
                caller=caller,
                result=None,
                error=exc,
                started_at=started,
            )
            last_err = exc
            if exc.retryable:
                logger.warning(
                    "ai.fallback_next",
                    provider=provider,
                    model=model,
                    reason=str(exc)[:160],
                )
                continue
            raise
        except Exception as exc:
            _record(
                operation=operation,
                provider=provider,
                model=model,
                caller=caller,
                result=None,
                error=exc,
                started_at=started,
            )
            raise
        _record(
            operation=operation,
            provider=provider,
            model=model,
            caller=caller,
            result=result,
            error=None,
            started_at=started,
        )
        return result, provider, model
    raise last_err or LLMError("IA_FALLBACK_CHAIN vazia", retryable=False)


# ---------------------------------------------------------------------------
# Capacidades genéricas do engine
# ---------------------------------------------------------------------------


def generate_text(
    prompt: str,
    *,
    caller: str,
    instruction: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    model: str | None = None,
) -> str:
    """Gera texto natural. Devolve a string já limpa."""

    async def attempt(client, m):
        return await client.text(
            prompt,
            model=m,
            instruction=instruction,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    result, _p, _m = _run(
        AiCall.Operation.TEXT, caller, attempt, providers.fallback_chain(model)
    )
    return _strip_think(result.content).strip('"')


def generate_json(
    prompt: str,
    *,
    caller: str,
    instruction: str | None = None,
    schema_description: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    model: str | None = None,
) -> dict:
    """Gera JSON estruturado. Devolve o dict já parseado (erra não-retryável se fugir do contrato)."""

    async def attempt(client, m):
        return await client.json(
            prompt,
            model=m,
            instruction=instruction,
            schema_description=schema_description,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    result, _p, _m = _run(
        AiCall.Operation.JSON, caller, attempt, providers.fallback_chain(model)
    )
    try:
        return json.loads(_strip_think(result.content))
    except json.JSONDecodeError as exc:
        raise LLMError(f"resposta não é JSON válido: {exc}", retryable=False) from exc


def chat(
    messages: list[dict],
    *,
    caller: str,
    temperature: float | None = None,
    max_tokens: int | None = None,
    json_mode: bool = False,
    model: str | None = None,
) -> str:
    """Chat multi-turn. Devolve o conteúdo da resposta do assistente."""

    async def attempt(client, m):
        return await client.chat(
            messages,
            model=m,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=json_mode,
        )

    result, _p, _m = _run(
        AiCall.Operation.CHAT, caller, attempt, providers.fallback_chain(model)
    )
    return _strip_think(result.content)


def summarize(
    text: str,
    *,
    caller: str,
    format: str = "paragraph",
    temperature: float | None = None,
    max_tokens: int | None = None,
    model: str | None = None,
) -> str:
    """Resume um texto (paragraph / bullets / headline). Devolve o resumo."""

    async def attempt(client, m):
        return await client.summarize(
            text, model=m, format=format, temperature=temperature, max_tokens=max_tokens
        )

    result, _p, _m = _run(
        AiCall.Operation.SUMMARIZE, caller, attempt, providers.fallback_chain(model)
    )
    return _strip_think(result.content)


def extract(
    text: str,
    *,
    json_schema: dict,
    caller: str,
    temperature: float | None = None,
    max_tokens: int | None = None,
    model: str | None = None,
) -> dict:
    """Extrai dados estruturados do texto conforme um JSON Schema. Devolve o dict parseado."""

    async def attempt(client, m):
        return await client.extract(
            text,
            model=m,
            json_schema=json_schema,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    result, _p, _m = _run(
        AiCall.Operation.EXTRACT, caller, attempt, providers.fallback_chain(model)
    )
    try:
        return json.loads(_strip_think(result.content))
    except json.JSONDecodeError as exc:
        raise LLMError(
            f"resposta não é JSON válido na extração: {exc}", retryable=False
        ) from exc


# ---------------------------------------------------------------------------
# Correção do training (grade() dentro da IA — decisão do Victor)
# ---------------------------------------------------------------------------


def grade(
    *,
    question: str,
    expected_answer: str,
    student_answer: str,
    caller: str,
    model: str | None = None,
) -> Grading:
    """Corrige a resposta de um trainee contra o gabarito: nota 0–10 + justificativa (≥6 = aprovado).

    Porte do contrato do legado. Nota grampeada em [0, 10]; sem justificativa => erra (não-retryável).
    """
    prompt = (
        f"ENUNCIADO DA MATERIA:\n{question.strip()}\n\n"
        f"GABARITO (resposta esperada):\n{expected_answer.strip()}\n\n"
        f"RESPOSTA DO TRAINEE:\n{student_answer.strip()}"
    )

    async def attempt(client, m):
        return await client.json(
            prompt,
            model=m,
            instruction=_GRADE_INSTRUCTION,
            schema_description=_GRADE_SCHEMA,
            temperature=0.2,
        )

    result, _p, _m = _run(
        AiCall.Operation.GRADE, caller, attempt, providers.fallback_chain(model)
    )
    try:
        data = json.loads(_strip_think(result.content))
        grade_value = max(0.0, min(10.0, float(data["nota"])))
        justification = str(data["justificativa"]).strip()
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise LLMError(
            f"resposta fora do contrato de correção: {exc}", retryable=False
        ) from exc
    if not justification:
        raise LLMError("IA devolveu nota sem justificativa", retryable=False)
    return Grading(grade=grade_value, justification=justification)


# ---------------------------------------------------------------------------
# Mídia (single-provider, SEM cadeia de fallback): Gemini visão/imagem, ElevenLabs TTS, Vision OCR.
# Cada uma grava 1 AiCall (tokens=0 — não se aplica). Imagem/áudio gerados vão pro media/ai/.
# ---------------------------------------------------------------------------


def _save_media(subdir: str, ext: str, data: bytes) -> str:
    """Salva bytes em MEDIA_ROOT/ia/<subdir>/<uuid>.<ext>. Devolve o caminho relativo ao MEDIA_ROOT."""
    import os
    import uuid

    folder = os.path.join(settings.MEDIA_ROOT, "ai", subdir)
    os.makedirs(folder, exist_ok=True)
    name = f"{uuid.uuid4().hex}.{ext}"
    with open(os.path.join(folder, name), "wb") as fh:
        fh.write(data)
    return f"ai/{subdir}/{name}"


def _media_call(*, operation: str, provider: str, model: str, caller: str, coro):
    """Roda uma chamada de mídia (corrotina sem args), grava AiCall (success/error), devolve o resultado."""
    started = time.monotonic()
    try:
        result = async_to_sync(coro)()
    except Exception as exc:
        _record(
            operation=operation,
            provider=provider,
            model=model,
            caller=caller,
            result=None,
            error=exc,
            started_at=started,
        )
        raise
    _record(
        operation=operation,
        provider=provider,
        model=model,
        caller=caller,
        result=None,
        error=None,
        started_at=started,
    )
    return result


def describe_image(
    image_bytes: bytes,
    *,
    caller: str,
    mime_type: str = "image/jpeg",
    prompt: str | None = None,
) -> str:
    """Visão: descreve/analisa uma imagem (selfie/documento/recibo). Devolve o texto.

    MiniMax-M3 é o PRIMÁRIO (multimodal, com o raciocínio <think> desligado); em falha cai pro Gemini
    (fallback). Grava 1 AiCall por tentativa.
    """
    from .minimax import MiniMaxClient

    mm = MiniMaxClient()

    async def mm_coro():
        return await mm.describe(image_bytes, mime_type=mime_type, prompt=prompt)

    try:
        return _media_call(
            operation=AiCall.Operation.VISION,
            provider="minimax",
            model=settings.MINIMAX_VISION_MODEL,
            caller=caller,
            coro=mm_coro,
        )
    except Exception as exc:  # noqa: BLE001 — MiniMax falhou → tenta o Gemini (fallback)
        logger.warning("ai.vision_fallback_gemini", error=str(exc)[:160])
        from .gemini import GeminiClient

        gemini = GeminiClient()

        async def gemini_coro():
            return await gemini.describe(
                image_bytes, mime_type=mime_type, prompt=prompt
            )

        return _media_call(
            operation=AiCall.Operation.VISION,
            provider="gemini",
            model=settings.GEMINI_VISION_MODEL,
            caller=caller,
            coro=gemini_coro,
        )


def generate_image(prompt: str, *, caller: str) -> str:
    """Gemini imagem: gera uma imagem a partir de um prompt. Salva em media/ai/image/ e devolve o caminho."""
    from .gemini import GeminiClient

    client = GeminiClient()

    async def coro():
        return await client.generate_image(prompt)

    raw, mime = _media_call(
        operation=AiCall.Operation.IMAGE,
        provider="gemini",
        model=settings.GEMINI_IMAGE_MODEL,
        caller=caller,
        coro=coro,
    )
    ext = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}.get(
        mime, "png"
    )
    return _save_media("image", ext, raw)


def _voice_for_gender(gender: str | None) -> str | None:
    """Voz do TTS pelo gênero do DESTINATÁRIO; outro/None → None (voz default do cliente).

    Regra de negócio (Victor): a voz é CRUZADA — destinatário homem recebe voz de mulher e
    vice-versa. Por isso `ELEVENLABS_VOICE_MALE` guarda a voz que o HOMEM recebe (na prática um
    voice-id feminino) e `ELEVENLABS_VOICE_FEMALE` a que a MULHER recebe (voice-id masculino). O
    nome fica "invertido" de propósito — NÃO 'corrigir' a inversão do `.env`.
    """
    if not gender:
        return None
    g = gender.strip().upper()
    # CRUZADA (regra do Victor): homem recebe voz FEMININA; mulher, voz MASCULINA.
    if g == "M":
        return settings.ELEVENLABS_VOICE_FEMALE
    if g == "F":
        return settings.ELEVENLABS_VOICE_MALE
    return None


def _minimax_voice_for_gender(gender: str | None) -> str:
    """Voz MiniMax pelo gênero do destinatário — CRUZADA (igual ElevenLabs, regra do Victor): o
    destinatário HOMEM recebe voz FEMININA (MINIMAX_VOICE_FEMALE=Portuguese_SereneWoman) e a MULHER
    recebe voz MASCULINA (MINIMAX_VOICE_MALE=Portuguese_GentleTeacher). Sem gênero → feminina (padrão)."""
    g = (gender or "").strip().upper()
    if g == "M":
        return settings.MINIMAX_VOICE_FEMALE
    if g == "F":
        return settings.MINIMAX_VOICE_MALE
    return settings.MINIMAX_VOICE_FEMALE


def tts(
    text: str, *, caller: str, voice_id: str | None = None, gender: str | None = None
) -> str:
    """TTS: gera áudio a partir do texto. Salva em media/ai/audio/ e devolve o caminho.

    ElevenLabs é o PRIMÁRIO (voz mais natural, Victor 2026-06-21); em falha cai pro MiniMax (fallback).
    A voz segue: `voice_id` explícito > voz por `gender` (M/F → ELEVENLABS_VOICE_* no primário;
    MINIMAX_VOICE_* no fallback) > voz default do provider. `voice_id` explícito casa com o provider ativo.
    """
    from .elevenlabs import ElevenLabsClient

    el = ElevenLabsClient()
    el_voice = voice_id or _voice_for_gender(gender)

    async def el_coro():
        return await el.tts(text, voice_id=el_voice)

    try:
        audio = _media_call(
            operation=AiCall.Operation.TTS,
            provider="elevenlabs",
            model=settings.ELEVENLABS_MODEL_ID,
            caller=caller,
            coro=el_coro,
        )
    except Exception as exc:  # noqa: BLE001 — ElevenLabs falhou → tenta o MiniMax (fallback)
        logger.warning("ai.tts_fallback_minimax", error=str(exc)[:160])
        from .minimax import MiniMaxClient

        mm = MiniMaxClient()
        mm_voice = voice_id or _minimax_voice_for_gender(gender)

        async def mm_coro():
            return await mm.tts(text, voice_id=mm_voice)

        audio = _media_call(
            operation=AiCall.Operation.TTS,
            provider="minimax",
            model=settings.MINIMAX_TTS_MODEL,
            caller=caller,
            coro=mm_coro,
        )
    return _save_media("audio", "mp3", audio)


def ocr(image_bytes: bytes, *, caller: str, document: bool = False) -> str:
    """Google Vision OCR: extrai o texto de uma imagem. Devolve o texto."""
    from .vision_ocr import VisionOCRClient

    client = VisionOCRClient()

    async def coro():
        return await client.detect_text(image_bytes, document=document)

    return _media_call(
        operation=AiCall.Operation.OCR,
        provider="google_vision",
        model="vision-v1",
        caller=caller,
        coro=coro,
    )
