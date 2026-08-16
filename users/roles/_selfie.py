"""Verificação de selfie por IA — compartilhada por `candidate` e `enrollment`.

"Do jeito melhor" (Victor 2026-06-05): pede um VEREDITO direto à visão — é selfie de pessoa real (não
foto-de-foto, tela, papel ou documento)? — e lê o começo da resposta (VALIDA/INVALIDA).

Mesma régua dos documentos do student (3 estados + revisão humana): a IA classifica em
**approved** (VALIDA) · **rejected** (INVALIDA → refazer) · **review** (IA fora do ar OU resposta
ambígua → o coordenador decide o sim/não). A IA sempre devolve um motivo (guardado como justificativa).
Uma implementação só, reusada (CONVENTION §12).
"""

from __future__ import annotations

import re

import structlog
from django.db import models

logger = structlog.get_logger()


class SelfieStatus(models.TextChoices):
    """Estados da validação da selfie (espelha StudentDocument.Validation). Usado por candidate/enrollment."""

    PENDING = "pending", "aguardando IA"
    APPROVED = "approved", "aprovada"
    REJECTED = "rejected", "reprovada (refazer)"
    REVIEW = "review", "em revisão (coordenador decide)"


APPROVED = SelfieStatus.APPROVED
REJECTED = SelfieStatus.REJECTED
REVIEW = SelfieStatus.REVIEW

_PROMPT = (
    "Você é o verificador de selfie de um cadastro escolar. A imagem é uma selfie de uma PESSOA "
    "REAL, fotografada ao vivo agora? Só reprove se for CLARAMENTE foto de outra foto, de tela, de "
    "papel ou de documento — em caso de dúvida, APROVE. Responda em UMA linha, começando com a "
    "palavra VALIDA ou INVALIDA (sem markdown, sem asteriscos), seguida de no máximo uma frase curta "
    "de motivo. NÃO escreva seu raciocínio e NÃO se corrija."
)


def verify(
    image_bytes: bytes, content_type: str, *, caller: str
) -> tuple[str, str | None]:
    """(status, justificativa). status ∈ approved|rejected|review. IA fora/ambígua → review (humano decide)."""
    from integrations.ai import service as ai

    try:
        desc = ai.describe_image(
            image_bytes, caller=caller, mime_type=content_type, prompt=_PROMPT
        )
    except Exception as exc:  # noqa: BLE001 — IA fora do ar → review (coordenador resolve)
        logger.warning("selfie_ai_failed", caller=caller, error=str(exc))
        return (
            REVIEW,
            "IA indisponível no momento — enviado para revisão manual do coordenador.",
        )
    # Lê o veredito FINAL, não o primeiro: modelos de raciocínio (MiniMax-M3) às vezes narram e se
    # corrigem ("INVALIDA… na verdade VALIDA"). `\b` evita casar "VALIDA" dentro de "INVALIDA".
    verdicts = re.findall(r"\b(?:IN)?VALIDA\b", (desc or "").upper())
    if not verdicts:
        return (
            REVIEW,
            desc,
        )  # resposta inconclusiva → revisão humana (nunca reprova no escuro)
    return (REJECTED if verdicts[-1].startswith("IN") else APPROVED), desc


# ── "somar" liveness + biometria facial (Victor 2026-06-05) ──────────────────────────────────────
# Chaves = VALORES de SelfieStatus (literais), pra casar tanto o membro-enum (liveness) quanto a string
# crua (biometria) sem depender de hash de Enum. min() → pior veredito vence.
_RANK = {"rejected": 0, "review": 1, "approved": 2}


def combine(*statuses: str) -> str:
    """Pior veredito vence no lattice approved > review > rejected (avança só se TODOS aprovam)."""
    return min(statuses, key=lambda s: _RANK.get(str(s), 1))


def add_face_match(
    *,
    user,
    selfie_image_path: str,
    caller: str,
    liveness_status: str,
    liveness_desc: str | None,
) -> tuple[str, str | None]:
    """SOMAR (Victor 2026-06-05): combina a liveness com o face-match biométrico (selfie × documento).

    Só roda se a liveness não reprovou de cara e a biometria estiver LIGADA (`BIOMETRIC_ENABLED`). Modelo
    fora / sem rosto / sem documento → o face-match devolve `review` (= bloqueio; o coordenador decide).
    Devolve (status_combinado, descrição_acumulada). Reuso único — candidate e enrollment chamam isto.
    """
    from django.conf import settings

    if liveness_status == REJECTED or not getattr(settings, "BIOMETRIC_ENABLED", True):
        return liveness_status, liveness_desc

    from integrations.tools.biometric import service as biometric

    fm = biometric.verify_identity(
        user=user, selfie_image_path=selfie_image_path, caller=caller
    )
    status = combine(liveness_status, fm.status)
    score = "—" if fm.score is None else f"{fm.score:.3f}"
    desc = f"{liveness_desc or ''} | biometria[{fm.status} score={score}]: {fm.reason}".strip(
        " |"
    )
    return status, desc


def instructions(
    image_bytes: bytes, content_type: str, *, reason: str | None, caller: str
) -> str | None:
    """Reprovou? A IA olha a foto DE NOVO e INSTRUI o que fazer pra ser aprovada (plan/13).

    Instruções curtas e práticas, faladas direto com a pessoa — vão no GET da selfie e no
    notify de reprovação. Best-effort: IA fora do ar → None (o motivo da reprovação já basta)."""
    from integrations.ai import service as ai

    prompt = (
        "Esta foto foi REPROVADA como selfie de verificação de identidade"
        + (f", pelo motivo: {reason}" if reason else "")
        + ". Olhe a imagem e dê instruções CURTAS e PRÁTICAS (2 a 3 frases, em português, "
        "falando diretamente com a pessoa) do que ela deve fazer pra nova foto ser aprovada — "
        "ex.: tirar a foto ao vivo segurando o celular (não fotografar outra foto ou tela), "
        "rosto inteiro visível e centralizado, boa iluminação, sem boné ou óculos escuros. "
        "Seja específico pro problema DESTA imagem; não repita o motivo, diga o que FAZER."
    )
    try:
        return ai.describe_image(
            image_bytes, caller=caller, mime_type=content_type, prompt=prompt
        )
    except Exception as exc:  # noqa: BLE001 — instrução é apoio; o motivo já foi guardado
        logger.warning(
            "selfie_instructions_failed", caller=caller, error=str(exc)[:200]
        )
        return None
