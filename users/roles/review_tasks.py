"""Sweep GLOBAL de documentos parados em análise (worker morto) → review (auditoria API B4).

Antes, `GET /reviews` (inbox do coordenador) chamava `_sweep_stale_reviews(hub)` em 3 serviços —
UPDATEs dentro de uma leitura (viola idempotência/safety HTTP: um retry/preflight/monitor batendo
no inbox promovia PENDING→REVIEW sem ação humana). O repo já tinha resolvido isso pra as SELFIES
(schedule `age_stale_*_selfies`); faltava o DOCUMENTO. Este task global fecha o gap, e os GETs
viram leitura pura.

RG e CNH são compartilhados entre os funis candidate e enrollment (mesmos models), então um sweep
global cobre os dois de uma vez. StudentDocument tem o seu (sinal de staleness = updated_at)."""

from __future__ import annotations

import structlog

logger = structlog.get_logger()


def age_stale_review_documents() -> int:
    """PENDING com TTL estourado → REVIEW, global (todos os polos). Idempotente. Registrado por
    `manage.py selfie_schedules`. Devolve quantos documentos foram envelhecidos."""
    from users.documents.models import CNH, RG
    from users.roles import _analysis

    def _started_at(doc):
        raw = (doc.validation_result or {}).get("analysis_started_at")
        return _analysis.started_at_from(raw, coerce_tz=False)

    aged = 0
    for model in (RG, CNH):
        before = model.objects.filter(validation_status=_analysis.REVIEW).count()
        _analysis.sweep_stale_documents(model.objects.all(), _started_at)
        aged += (
            model.objects.filter(validation_status=_analysis.REVIEW).count() - before
        )

    aged += _age_stale_student_documents()
    logger.info("review.documents_aged", aged=aged)
    return aged


def _age_stale_student_documents() -> int:
    """StudentDocument PENDING estourado → REVIEW (staleness por updated_at, como o sweep antigo)."""
    from datetime import timedelta

    from django.utils import timezone

    from users.roles import _analysis
    from users.roles.student.models import StudentDocument

    cutoff = timezone.now() - timedelta(seconds=_analysis.ttl_seconds())
    return StudentDocument.objects.filter(
        validation_status=StudentDocument.Validation.PENDING,
        updated_at__lt=cutoff,
    ).update(validation_status=StudentDocument.Validation.REVIEW)
