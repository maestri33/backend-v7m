"""Sweep global de documento parado → review (auditoria API B4).

O sweep saiu do GET /reviews (era write numa leitura) pro schedule global
`age_stale_review_documents`. Estes testes provam: (1) o sweep global envelhece um RG PENDING
estourado; (2) o GET list_document_reviews_for_hub NÃO escreve mais (documento pending recente
não vira review só por ser listado)."""

import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

pytestmark = pytest.mark.django_db


def _mk_candidate_with_pending_rg(*, started_ago_seconds: int):
    from hub.models import Hub
    from users.address.models import Address
    from users.auth.models import User
    from users.documents.models import RG, Document
    from users.roles import _analysis
    from users.roles.candidate.models import Candidate

    coord = User.objects.create_user(external_id=uuid.uuid4(), is_active=True)
    hub = Hub.objects.create(
        brand="standard", address=Address.objects.create(), coordinator=coord
    )
    u = User.objects.create_user(external_id=uuid.uuid4(), is_active=True)
    cand = Candidate.objects.create(user=u, hub=hub, doc_type="rg")
    doc = Document.objects.create(user=u)
    started = (timezone.now() - timedelta(seconds=started_ago_seconds)).isoformat()
    rg = RG.objects.create(
        document=doc,
        validation_status=_analysis.PENDING,
        validation_result={"analysis_started_at": started},
    )
    return hub, cand, rg


def test_sweep_global_envelhece_rg_estourado():
    from users.roles import _analysis
    from users.roles.review_tasks import age_stale_review_documents

    _hub, _cand, rg = _mk_candidate_with_pending_rg(
        started_ago_seconds=_analysis.ttl_seconds() + 60
    )
    aged = age_stale_review_documents()
    rg.refresh_from_db()
    assert aged >= 1
    assert rg.validation_status == _analysis.REVIEW


def test_get_reviews_nao_escreve_mais():
    """Pureza HTTP (B4): listar o inbox com um RG pending RECENTE (não estourado) não pode
    promovê-lo a review — o write saiu do GET."""
    from users.roles import _analysis
    from users.roles.candidate import service as cs

    hub, _cand, rg = _mk_candidate_with_pending_rg(started_ago_seconds=1)  # fresco
    cs.list_document_reviews_for_hub(hub=hub)  # o GET
    rg.refresh_from_db()
    assert rg.validation_status == _analysis.PENDING, (
        "o GET mutou o documento (write numa leitura)"
    )
