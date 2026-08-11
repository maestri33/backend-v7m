"""Semântica HTTP da selfie: o `GET /candidate/selfie` e o `GET /enrollment/selfie` aplicavam o TTL
NA LEITURA — uma selfie `pending` estourada virava `review` + notificava o coordenador DENTRO do GET
(um retry/preflight/crawler disparava a transição; viola idempotência/safety HTTP).

Aqui: (a) o GET é PURO (não muta status nem notifica), e (b) o job `age_stale_selfies` faz a transição
pending→review + notify quando o TTL estourou, idempotente (rodar 2× não duplica o notify).
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

pytestmark = pytest.mark.django_db


def _stale_taken_at():
    from users.roles import _analysis

    # bem além do TTL (default 120s) → is_stale == True
    return timezone.now() - timedelta(seconds=_analysis.ttl_seconds() + 600)


def _hub():
    from hub.models import Hub
    from users.address.models import Address
    from users.auth.models import User
    from users.profiles.models import Profile

    coord = User.objects.create_user(external_id=uuid.uuid4())
    Profile.objects.create(
        user=coord, cpf=str(uuid.uuid4().int)[:11], phone=str(uuid.uuid4().int)[:13]
    )
    addr = Address.objects.create(city="São Paulo", state="SP")
    return Hub.objects.create(
        address=addr, brand="test", coordinator=coord, is_default=False
    )


def _candidate(*, stale: bool):
    from users.auth.models import User
    from users.roles import _selfie
    from users.roles.candidate.models import Candidate

    user = User.objects.create_user(external_id=uuid.uuid4())
    return Candidate.objects.create(
        user=user,
        hub=_hub(),
        status=Candidate.Status.SELFIE,
        selfie_image="s.jpg",
        selfie_status=_selfie.SelfieStatus.PENDING,
        selfie_taken_at=_stale_taken_at() if stale else timezone.now(),
    )


def _enrollment(*, stale: bool):
    from users.auth.models import User
    from users.roles import _selfie
    from users.roles.enrollment.models import Enrollment

    user = User.objects.create_user(external_id=uuid.uuid4())
    promoter = User.objects.create_user(external_id=uuid.uuid4())
    return Enrollment.objects.create(
        user=user,
        promoter=promoter,
        hub=_hub(),
        status=Enrollment.Status.SELFIE,
        selfie_image="s.jpg",
        selfie_status=_selfie.SelfieStatus.PENDING,
        selfie_taken_at=_stale_taken_at() if stale else timezone.now(),
    )


# ---------------------------------------------------------------- (a) GET é PURO


def test_get_candidate_selfie_nao_muta_nem_notifica(monkeypatch):
    from users.roles import _selfie
    from users.roles.candidate import service as cs
    from users.roles.candidate.models import Candidate

    cand = _candidate(stale=True)  # pending estourado — o pior caso do bug
    calls = []
    monkeypatch.setattr(cs, "_notify_selfie_review", lambda c: calls.append(c))

    cs.get_selfie(user_external_id=str(cand.user.external_id))

    cand.refresh_from_db()
    assert cand.selfie_status == _selfie.SelfieStatus.PENDING, (
        "GET envelheceu a selfie (mutação numa leitura)"
    )
    assert calls == [], "GET notificou o coordenador (efeito colateral numa leitura)"
    # sanity: o objeto ainda está pending no banco (nada foi salvo como review)
    assert (
        Candidate.objects.get(pk=cand.pk).selfie_status == _selfie.SelfieStatus.PENDING
    )


def test_get_enrollment_selfie_nao_muta_nem_notifica(monkeypatch):
    from users.roles import _selfie
    from users.roles.enrollment import service as es

    enr = _enrollment(stale=True)
    calls = []
    monkeypatch.setattr(es, "_notify_selfie_review", lambda e: calls.append(e))

    es.get_selfie(user_external_id=str(enr.user.external_id))

    enr.refresh_from_db()
    assert enr.selfie_status == _selfie.SelfieStatus.PENDING
    assert calls == []


# ------------------------------------------------- (b) o job faz a transição + é idempotente


def test_age_stale_selfies_candidate_transiciona_e_notifica_idempotente(monkeypatch):
    from users.roles import _selfie
    from users.roles.candidate import service as cs

    cand = _candidate(stale=True)
    fresh = _candidate(stale=False)  # dentro do TTL — o job NÃO pode tocar
    calls = []
    monkeypatch.setattr(cs, "_notify_selfie_review", lambda c: calls.append(c.pk))

    assert cs.age_stale_selfies() == 1  # só o estourado
    cand.refresh_from_db()
    fresh.refresh_from_db()
    assert cand.selfie_status == _selfie.SelfieStatus.REVIEW
    assert fresh.selfie_status == _selfie.SelfieStatus.PENDING
    assert calls == [cand.pk]

    # idempotente: 2ª passada não repega (já é review) → nada envelhece, notify não duplica
    assert cs.age_stale_selfies() == 0
    assert calls == [cand.pk]


def test_age_stale_selfies_enrollment_transiciona_e_notifica_idempotente(monkeypatch):
    from users.roles import _selfie
    from users.roles.enrollment import service as es

    enr = _enrollment(stale=True)
    fresh = _enrollment(stale=False)
    calls = []
    monkeypatch.setattr(es, "_notify_selfie_review", lambda e: calls.append(e.pk))

    assert es.age_stale_selfies() == 1
    enr.refresh_from_db()
    fresh.refresh_from_db()
    assert enr.selfie_status == _selfie.SelfieStatus.REVIEW
    assert fresh.selfie_status == _selfie.SelfieStatus.PENDING
    assert calls == [enr.pk]

    assert es.age_stale_selfies() == 0
    assert calls == [enr.pk]


# ============================================================ RG: mesma regra da selfie
# O RG tinha o MESMO bug que a selfie já corrigira, mas num caminho diferente: `me_dict` (GET
# /veteran/me, GET /me) e `get_rg_section` chamavam `_reconcile_stale_analyses` NA LEITURA — um RG
# `pending` estourado virava `review` + `_notify_rg_review(coord)` DENTRO do GET. Agora o flip mora
# no job `age_stale_rg` e os GETs são puros.


def _enrollment_with_stale_rg(*, stale: bool):
    from users.auth.models import User
    from users.documents import service as documents_service
    from users.roles.enrollment.models import Enrollment

    user = User.objects.create_user(external_id=uuid.uuid4())
    promoter = User.objects.create_user(external_id=uuid.uuid4())
    enr = Enrollment.objects.create(
        user=user,
        promoter=promoter,
        hub=_hub(),
        status=Enrollment.Status.RG,
    )
    doc = documents_service.create_empty(
        user
    )  # provisiona Document + os 5 sub-docs (como o real)
    # `analysis_started_at` derivado de timezone.now() → casa com USE_TZ (aware ou naive) do projeto.
    started = _stale_taken_at() if stale else timezone.now()
    doc.rg.validation_status = doc.rg.Validation.PENDING
    doc.rg.validation_result = {"analysis_started_at": started.isoformat()}
    doc.rg.save(update_fields=["validation_status", "validation_result"])
    return enr


def _rg_status(enr):
    from users.documents.models import RG

    return RG.objects.get(document__user=enr.user).validation_status


def test_get_rg_section_nao_muta_nem_notifica(monkeypatch):
    from users.documents.models import RG
    from users.roles.enrollment import service as es

    enr = _enrollment_with_stale_rg(stale=True)  # pending estourado — o pior caso
    calls = []
    monkeypatch.setattr(es, "_notify_rg_review", lambda e, reason: calls.append(e.pk))

    es.get_rg_section(user_external_id=str(enr.user.external_id))

    assert _rg_status(enr) == RG.Validation.PENDING, (
        "GET /rg envelheceu o RG (mutação numa leitura)"
    )
    assert calls == [], (
        "GET /rg notificou o coordenador (efeito colateral numa leitura)"
    )


def test_age_stale_rg_transiciona_e_notifica_idempotente(monkeypatch):
    from users.documents.models import RG
    from users.roles.enrollment import service as es

    enr = _enrollment_with_stale_rg(stale=True)
    fresh = _enrollment_with_stale_rg(
        stale=False
    )  # dentro do TTL — o job NÃO pode tocar
    calls = []
    monkeypatch.setattr(es, "_notify_rg_review", lambda e, reason: calls.append(e.pk))

    assert es.age_stale_rg() == 1  # só o estourado
    assert _rg_status(enr) == RG.Validation.REVIEW
    assert _rg_status(fresh) == RG.Validation.PENDING
    assert calls == [enr.pk]

    # idempotente: 2ª passada não repega (já é review) → notify não duplica
    assert es.age_stale_rg() == 0
    assert calls == [enr.pk]


# =================================================== candidate: doc (RG/CNH) tem a mesma regra
# `GET /candidate/document` (get_document_section) chamava `_reconcile_stale_analyses` NA LEITURA —
# um doc `pending` estourado virava `review` DENTRO do GET (mutação numa leitura; candidate flipa
# CALADO — sem notify, ≠ enrollment). Agora o flip mora no job `age_stale_docs` e o GET é puro.


def _candidate_with_stale_doc(*, stale: bool):
    from users.auth.models import User
    from users.documents import service as documents_service
    from users.roles import _selfie
    from users.roles.candidate.models import Candidate

    user = User.objects.create_user(external_id=uuid.uuid4())
    cand = Candidate.objects.create(
        user=user,
        hub=_hub(),
        status=Candidate.Status.SELFIE,
        selfie_image="s.jpg",
        selfie_status=_selfie.SelfieStatus.PENDING,
        selfie_taken_at=timezone.now(),
        doc_type="rg",  # candidato escolhe RG OU CNH; aqui RG
    )
    doc = documents_service.create_empty(user)  # Document + os 5 sub-docs (como o real)
    started = _stale_taken_at() if stale else timezone.now()
    doc.rg.validation_status = doc.rg.Validation.PENDING
    doc.rg.validation_result = {"analysis_started_at": started.isoformat()}
    doc.rg.save(update_fields=["validation_status", "validation_result"])
    return cand


def _cand_doc_status(cand):
    from users.documents.models import RG

    return RG.objects.get(document__user=cand.user).validation_status


def test_get_candidate_document_nao_muta():
    from users.documents.models import RG
    from users.roles.candidate import service as cs

    cand = _candidate_with_stale_doc(stale=True)  # pending estourado — o pior caso

    cs.get_document_section(user_external_id=str(cand.user.external_id))

    assert _cand_doc_status(cand) == RG.Validation.PENDING, (
        "GET /candidate/document envelheceu o doc (mutação numa leitura)"
    )


def test_age_stale_docs_transiciona_idempotente():
    from users.documents.models import RG
    from users.roles.candidate import service as cs

    cand = _candidate_with_stale_doc(stale=True)
    fresh = _candidate_with_stale_doc(
        stale=False
    )  # dentro do TTL — o job NÃO pode tocar

    assert cs.age_stale_docs() == 1  # só o estourado
    assert _cand_doc_status(cand) == RG.Validation.REVIEW
    assert _cand_doc_status(fresh) == RG.Validation.PENDING

    assert cs.age_stale_docs() == 0  # idempotente: já é review, não repega
