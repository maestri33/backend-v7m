"""Router da Central de Análises / Reviews do Polo (Coordenador de Polo)."""

from __future__ import annotations

from ninja import Router

from api.auth import require_roles
from api.leadership.schemas import (
    AddressProofDecideOut,
    CandidateDetailOut,
    CandidateMeOut,
    CandidateSelfieDecideOut,
    CandidateSelfieDetailOut,
    EnrollmentRgDecideOut,
    EnrollmentSelfieDecideOut,
    ReviewsOut,
    SelfieDecideIn,
)
from hub import interface as hub_iface
from users.auth.models import User
from users.exceptions import Forbidden
from users.roles.candidate import service as candidate_iface
from users.roles.enrollment import service as enrollment_iface
from users.roles.student import service as student_iface
from users.roles.training import service as training_iface

router = Router(tags=["review"])

NOT_COORDINATOR_DETAIL = (
    "Você não pode entrar como coordenador: não coordena nenhum polo. "
    "Faça seu login na área da sua função."
)


def _coordinator(request) -> User:
    require_roles(request.auth, "coordinator")
    user = User.objects.filter(
        external_id=request.auth.external_id, is_active=True
    ).first()
    if user is None:
        raise Forbidden("Coordenador não encontrado.", code="FORBIDDEN_ROLE")
    return user


def _coordinator_hub(coordinator: User):
    hub = hub_iface.coordinated_by(coordinator)
    if hub is None:
        raise Forbidden(NOT_COORDINATOR_DETAIL, code="NOT_HUB_COORDINATOR")
    return hub


@router.get("/reviews", response=ReviewsOut, summary="Central unificada de revisões do polo")
def list_reviews(request):
    """TUDO que espera análise/decisão do coordenador no polo."""
    coordinator = _coordinator(request)
    hub = _coordinator_hub(coordinator)
    enrollment_reviews = enrollment_iface.list_reviews_for_hub(hub=hub)

    def _norm(item: dict, type_: str, kind: str) -> dict:
        return {
            "external_id": item.get("external_id"),
            "type": type_,
            "kind": kind,
            **item,
        }

    return {
        "enrollment_rg": [
            _norm(i, "enrollment", "rg") for i in enrollment_reviews["rg"]
        ],
        "enrollment_selfie": [
            _norm(i, "enrollment", "selfie") for i in enrollment_reviews["selfie"]
        ],
        "candidate_document": [
            _norm(i, "candidate", "document")
            for i in candidate_iface.list_document_reviews_for_hub(hub=hub)
        ],
        "candidate_selfie": [
            _norm(i, "candidate", "selfie")
            for i in candidate_iface.list_selfie_reviews_for_hub(hub=hub)
        ],
        "student_documents": [
            {
                "external_id": i["document_external_id"],
                "type": "student",
                "kind": "document",
                "student_external_id": i.get("student_external_id"),
                "document_external_id": i.get("document_external_id"),
                "name": i.get("name"),
                "doc_type": i.get("doc_type"),
                "since": i.get("since"),
            }
            for i in student_iface.list_document_reviews_for_hub(hub=hub)
        ],
        "candidates_awaiting_approval": [
            _norm(i, "candidate", "awaiting_approval")
            for i in candidate_iface.list_awaiting_approval_for_hub(hub=hub)
        ],
        "locked_promoters": [
            {
                "external_id": i["promoter_external_id"],
                "type": "promoter",
                "kind": "locked_training",
                "promoter_external_id": i.get("promoter_external_id"),
                "name": i.get("name"),
                "pending_materials": i.get("pending_materials"),
            }
            for i in training_iface.list_locked_promoters_for_hub(hub=hub)
        ],
    }


@router.post(
    "/enrollments/{external_id}/rg/decide",
    response=EnrollmentRgDecideOut,
    summary="Decisão de RG em revisão",
)
def decide_enrollment_rg(request, external_id: str, payload: SelfieDecideIn):
    """Coordenador decide o RG de uma matrícula em revisão."""
    coordinator = _coordinator(request)
    return enrollment_iface.decide_rg(
        enrollment_external_id=external_id,
        coordinator=coordinator,
        approve=payload.approve,
        reason=payload.reason,
    )


@router.post(
    "/enrollments/{external_id}/address-proof/decide",
    response=AddressProofDecideOut,
    summary="Decisão de comprovante de residência",
)
def decide_enrollment_address_proof(request, external_id: str, payload: SelfieDecideIn):
    """Coordenador decide a justificativa de parentesco do comprovante."""
    coordinator = _coordinator(request)
    return enrollment_iface.decide_address_proof_kinship(
        enrollment_external_id=external_id,
        coordinator=coordinator,
        approve=payload.approve,
        reason=payload.reason,
    )


@router.post(
    "/enrollments/{external_id}/selfie/decide",
    response=EnrollmentSelfieDecideOut,
    summary="Decisão de selfie de matrícula",
)
def decide_enrollment_selfie(request, external_id: str, payload: SelfieDecideIn):
    """Coordenador decide a selfie de matrícula em revisão."""
    coordinator = _coordinator(request)
    enr = enrollment_iface.decide_selfie(
        enrollment_external_id=external_id,
        coordinator=coordinator,
        approve=payload.approve,
        reason=payload.reason,
    )
    return {
        "external_id": str(enr.external_id),
        "selfie_status": enr.selfie_status,
        "selfie_verified": enr.selfie_verified,
        "status": enr.status,
    }


@router.post(
    "/candidates/{external_id}/selfie/decide",
    response=CandidateSelfieDecideOut,
    summary="Decisão de selfie de candidato",
)
def decide_candidate_selfie(request, external_id: str, payload: SelfieDecideIn):
    """Coordenador decide a selfie de candidato em revisão."""
    coordinator = _coordinator(request)
    cand = candidate_iface.decide_selfie(
        candidate_external_id=external_id,
        coordinator=coordinator,
        approve=payload.approve,
        reason=payload.reason,
    )
    return {
        "external_id": str(cand.external_id),
        "selfie_status": cand.selfie_status,
        "status": cand.status,
    }


@router.get(
    "/candidates/{external_id}/selfie",
    response=CandidateSelfieDetailOut,
    summary="Detalhe da selfie do candidato",
)
def get_candidate_selfie_for_coordinator(request, external_id: str):
    """Detalhe da selfie em revisão para decisão humana."""
    coordinator = _coordinator(request)
    return candidate_iface.candidate_selfie_for_coordinator(
        candidate_external_id=external_id, coordinator=coordinator
    )


@router.post(
    "/candidates/{external_id}/document/decide",
    response=CandidateMeOut,
    summary="Decisão de documento de candidato",
)
def decide_candidate_document(request, external_id: str, payload: SelfieDecideIn):
    """Coordenador decide o documento em revisão do candidato."""
    coordinator = _coordinator(request)
    return candidate_iface.decide_document(
        candidate_external_id=external_id,
        coordinator=coordinator,
        approve=payload.approve,
        reason=payload.reason,
    )


@router.post(
    "/candidates/{external_id}/document/reset",
    response=CandidateMeOut,
    summary="Destravar tipo de documento",
)
def reset_candidate_doc_type(request, external_id: str):
    """Zera o doc_type para destravar candidato que fixou tipo errado."""
    coordinator = _coordinator(request)
    return candidate_iface.reset_doc_type(
        candidate_external_id=external_id, coordinator=coordinator
    )
