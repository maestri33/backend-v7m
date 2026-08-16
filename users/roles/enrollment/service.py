"""Lógica do enrollment (matrícula).

- **6a** — nascimento (`create_from_lead`, chamado pelo hook do lead pago).
- **6b** — funil de coleta DOCUMENTO-PRIMEIRO (`rg → address → education → selfie` até
  `awaiting_release`); a extração do RG povoa o perfil (plan/13). A etapa `started`/perfil não existe mais.
- **6c** — fase da taxa + `conclude` do coordenador (plan/14): paga/agenda as 2 parcelas e promove
  `enrollment→student` (síncrono, não espera o pagamento; o `release` antigo foi removido).

Provado real fim-a-fim (RG/selfie reais, plan/13 2026-06-11; student→veteran 2026-06-06). Reusa
`users/address`, `users/documents`, `integrations/ai` (visão da selfie, best-effort), `users/roles`, `notify`.
"""

from __future__ import annotations

import structlog
from django.conf import settings
from django.db import transaction

from users.address import interface as address_iface
from users.documents import interface as documents_iface
from users.exceptions import Conflict, DomainError, NotFound
from users.profiles import interface as profiles
from users.roles import interface as roles
from users.roles.enrollment.models import EducationalData, Enrollment

logger = structlog.get_logger()

_S = Enrollment.Status
_SELFIE_EXT = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}


class EnrollmentError(DomainError):
    """Erro de borda do enrollment (não encontrada, etapa fora de ordem, gate de status/coordenador).

    É `DomainError` (422): o handler central da API converte em JSON `{detail, code, …extra}`."""

    status = 422


# ── 6a: nascimento (chamado pelo hook do lead) ──────────────────────────────


def create_from_lead(*, user, promoter, hub, self_study=False) -> Enrollment:
    """Cria o Enrollment(RG — documento primeiro, plan/13) ligado ao HUB herdado + promove a role
    `lead→enrollment`. Idempotente.

    Chamado DENTRO da transação do hook de pagamento (lead pago). Se o enrollment já existe (webhook
    re-tentou), devolve o existente sem duplicar nem re-promover.
    """
    existing = Enrollment.objects.filter(user=user).first()
    if existing is not None:
        return existing

    enrollment = Enrollment.objects.create(
        user=user,
        promoter=promoter,
        hub=hub,
        self_study=self_study,
        status=Enrollment.Status.RG,
    )
    if "enrollment" not in roles.active_roles(user):
        roles.promote(user, "enrollment")

    logger.info(
        "enrollment.created_from_lead",
        external_id=str(enrollment.external_id),
        hub=str(hub.external_id),
    )
    return enrollment


def get_by_external_id(external_id: str) -> Enrollment | None:
    return (
        Enrollment.objects.filter(external_id=external_id)
        .select_related("hub", "promoter", "user")
        .first()
    )


def get_for_user_external_id(user_external_id: str) -> Enrollment | None:
    """A matrícula do usuário logado (borda autenticada do funil)."""
    return (
        Enrollment.objects.filter(user__external_id=user_external_id)
        .select_related("hub", "promoter", "user")
        .first()
    )


def _require(user_external_id: str, *allowed_status) -> Enrollment:
    """Carrega a matrícula do usuário e exige (se `allowed_status`) que esteja numa etapa permitida."""
    enr = (
        Enrollment.objects.filter(user__external_id=user_external_id)
        .select_related("hub", "promoter", "user")
        .first()
    )
    if enr is None:
        raise NotFound("Matrícula não encontrada.", code="ENROLLMENT_NOT_FOUND")
    if allowed_status and enr.status not in allowed_status:
        # 409 + expected_status = a etapa ATUAL no servidor — o front roteia o wizard com isso.
        # `public_status`: o ALUNO nunca vê a fase da taxa (plan/14 — política interna do polo).
        raise Conflict(
            "Sua matrícula está em outra etapa.",
            code="WRONG_STATUS",
            extra={"expected_status": public_status(enr)},
        )
    return enr


def _set_status(enr: Enrollment, to_status: str) -> None:
    enr.status = to_status
    enr.save(update_fields=["status", "updated_at"])


def _rg_started_at(rg):
    """Quando a análise do RG (re)começou — do JSON do reset (proposta #2). None = sem referência."""
    from datetime import datetime

    raw = (rg.validation_result or {}).get("analysis_started_at") if rg else None
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except (ValueError, TypeError):
        return None


def _reconcile_stale_analyses(enr: Enrollment) -> None:
    """TTL guard (proposta #2): `pending` que estourou o prazo vira `review` — o aluno nunca fica
    preso em "analisando…" se a task da IA morreu. Persiste o flip (cai na fila do coordenador) e
    avisa, reusando os mesmos caminhos do review da IA. Idempotente; roda nas LEITURAS do funil."""
    from users.roles import _analysis, _selfie

    if _analysis.is_stale(enr.selfie_status, enr.selfie_taken_at):
        enr.selfie_status = _selfie.REVIEW
        enr.selfie_description = (
            enr.selfie_description or ""
        ).strip() or _analysis.stale_reason()
        enr.save(update_fields=["selfie_status", "selfie_description", "updated_at"])
        _notify_selfie_review(enr)

    rg = documents_iface.get_rg(str(enr.user.external_id))
    if rg is not None and _analysis.is_stale(rg.validation_status, _rg_started_at(rg)):
        _finish_rg(
            enr,
            rg,
            _analysis.REVIEW,
            _analysis.stale_reason(),
            rg.validation_result or {},
        )


def public_status(enr: Enrollment) -> str:
    """Status na visão do ALUNO. A fase da taxa (`fee_paid`/`fee_scheduled`) é INTERNA do polo
    (plan/14, Victor 2026-06-12: o aluno NUNCA sabe da taxa) — pra ele aparece `awaiting_release`."""
    if enr.status in (_S.FEE_PAID, _S.FEE_SCHEDULED):
        return _S.AWAITING_RELEASE
    return enr.status


def to_dict(enr: Enrollment) -> dict:
    return {
        "external_id": str(enr.external_id),
        "status": public_status(enr),
        "hub_external_id": str(enr.hub.external_id),
        "selfie_verified": enr.selfie_verified,
        "selfie_status": enr.selfie_status,
        # canônico unificado (proposta #4): a análise da SELFIE/assinatura sob o nome `analysis_status`.
        # `selfie_status` segue como alias (compat) até o front migrar.
        "analysis_status": enr.selfie_status,
    }


def me_dict(enr: Enrollment) -> dict:
    """GET /me RICO (auditoria do front 2026-06-10): o resume do wizard pré-preenche TODAS as seções
    numa chamada só. Bloco `None` = seção ainda não preenchida; `address_complete` = endereço pronto."""
    _reconcile_stale_analyses(
        enr
    )  # TTL guard (proposta #2): pending estourado → review, aqui também
    user_ext = str(enr.user.external_id)
    p = profiles.get(enr.user)

    profile = None
    if p and any(
        (
            p.mother_name,
            p.father_name,
            p.marital_status,
            p.birthplace,
            p.nationality,
        )
    ):
        profile = {
            "mother_name": p.mother_name,
            "father_name": p.father_name,
            "marital_status": p.marital_status,
            "birthplace": p.birthplace,
            "nationality": p.nationality,
        }

    rg_data = (documents_iface.get_by_external_id(user_ext) or {}).get("rg") or {}
    rg = None
    if any(
        rg_data.get(k) for k in ("number", "front_photo", "back_photo", "full_photo")
    ):
        rg = {
            "number": rg_data.get("number"),
            "issuing_agency": rg_data.get("issuing_agency"),
            "issue_date": rg_data.get("issue_date"),
            "front_photo": rg_data.get("front_photo"),
            "back_photo": rg_data.get("back_photo"),
            "full_photo": rg_data.get("full_photo"),
            # validação IA (plan/12): o front mostra "analisando…"/motivo e o que falta digitar.
            # `analysis_status`/`analysis_reason` = nome CANÔNICO (proposta #4); os `validation_*`
            # seguem como alias (compat) até o front migrar.
            "analysis_status": rg_data.get("validation_status"),
            "analysis_reason": rg_data.get("validation_reason"),
            "validation_status": rg_data.get("validation_status"),
            "validation_reason": rg_data.get("validation_reason"),
            # MESMA régua da seção (doc + perfil) — é a lista que trava a selfie (proposta #10)
            "missing_fields": [
                *(f for f in _RG_DOC_FIELDS if not rg_data.get(f)),
                *(f for f in _RG_PROFILE_FIELDS if not getattr(p, f, None)),
            ],
        }

    try:
        edu = enr.educational_data
    except EducationalData.DoesNotExist:
        edu = None
    education = None
    if edu is not None:
        education = {
            "level": edu.level,
            "grade": edu.grade,
            "completed": edu.completed,
            "last_school": edu.last_school,
            "city": edu.city,
            "state": edu.state,
            "last_year_when": edu.last_year_when,
        }

    address = address_iface.get_by_external_id(user_ext)
    return {
        **to_dict(enr),
        "profile": profile,
        "address_complete": address_iface.is_complete(address),
        # blocos COMPLETOS de address e selfie (proposta #3): o /me vira a resposta canônica única —
        # toda mutação devolve este shape e o front nunca re-fetcha pra rotear.
        "address": _address_dict(user_ext),
        "selfie": _selfie_dict(enr),
        "rg": rg,
        "education": education,
    }


# ── 6b: funil de coleta ──────────────────────────────────────────────────────
# `status` = a seção que o aluno preenche AGORA (vocabulário do wizard do front).
# Ordem nova (plan/13, Victor 2026-06-11): DOCUMENTO primeiro — a extração povoa o perfil.
# rg → address → education → selfie → awaiting_release → completed.
# Gates ESTRITOS nos POSTs de seção; fora de hora → 409 {detail, code:WRONG_STATUS,
# expected_status}. GETs são leitura (qualquer etapa). Avanço com CHAIN-SKIP: se a próxima
# seção já está completa, pula — ninguém fica preso numa etapa sem nada a fazer.


def _has_education(enr: Enrollment) -> bool:
    try:
        return enr.educational_data is not None
    except EducationalData.DoesNotExist:
        return False


def _advance_to(enr: Enrollment, target: str) -> None:
    """Avança pra `target` PULANDO seções já completas (chain-skip, plan/13)."""
    user_ext = str(enr.user.external_id)
    status = target
    while True:
        if status == _S.ADDRESS and address_iface.is_complete(
            address_iface.get_by_external_id(user_ext)
        ):
            status = _S.EDUCATION
            continue
        if status == _S.EDUCATION and _has_education(enr):
            status = _S.SELFIE
            continue
        break
    _set_status(enr, status)


# ── seção ENDEREÇO (plan/13): POST só com CEP · PATCH só-vazios · GET ────────
# `missing_fields` em toda resposta: o front renderiza input SÓ do que está na lista
# (ex.: ["number"] = ViaCEP achou tudo, falta o número; rua/bairro na lista = CEP único).
_ADDRESS_FIELDS = (
    "street",
    "number",
    "neighborhood",
    "city",
    "state",
)  # complement opcional


def _address_dict(user_external_id: str) -> dict:
    data = address_iface.as_public_dict(
        address_iface.get_by_external_id(user_external_id)
    )
    data["missing_fields"] = [f for f in _ADDRESS_FIELDS if not data.get(f)]
    return data


def get_address(*, user_external_id: str) -> dict:
    """GET do endereço + `missing_fields` (o que ainda falta preencher)."""
    _require(user_external_id)
    return _address_dict(user_external_id)


def set_address_cep(*, user_external_id: str, cep: str) -> dict:
    """POST do endereço (plan/13): body só `{cep}`. Acha no ViaCEP, grava e devolve o **EnrollmentMe
    canônico** (proposta #3) — o bloco `address.missing_fields` JÁ AVISA o que falta: rua achada →
    `["number"]`; cidade de CEP único → rua/bairro/número."""
    enr = _require(user_external_id, _S.ADDRESS)
    address_iface.set_by_cep(external_id=user_external_id, cep=cep)
    _advance_address(enr, user_external_id)
    return me_dict(enr)


def set_address_data(*, user_external_id: str, **fields) -> dict:
    """PATCH do endereço — preenche SÓ o que está VAZIO (não sobrescreve o que o CEP trouxe).
    Devolve o EnrollmentMe canônico (proposta #3)."""
    enr = _require(user_external_id, _S.ADDRESS)
    address_iface.fill_empty(external_id=user_external_id, **fields)
    _advance_address(enr, user_external_id)
    return me_dict(enr)


def _advance_address(enr: Enrollment, user_external_id: str) -> None:
    """Endereço completo → EDUCATION (ordem plan/13), com chain-skip."""
    if enr.status == _S.ADDRESS and address_iface.is_complete(
        address_iface.get_by_external_id(user_external_id)
    ):
        _advance_to(enr, _S.EDUCATION)


# ── seção DOCUMENTO (plan/13): GET rico · PATCH completa/corrige ─────────────
# Campos editáveis: do RG (number/órgão/emissão) + os de perfil que o documento povoa
# (filiação/naturalidade) ou não traz (estado civil/nacionalidade). `name`/`birth_date`
# vêm do CPFHub/extração — NÃO editáveis pelo aluno.
_RG_DOC_FIELDS = ("number", "issuing_agency", "issue_date")
_RG_PROFILE_FIELDS = (
    "mother_name",
    "father_name",
    "birthplace",
    "marital_status",
    "nationality",
)


def _rg_section_dict(enr: Enrollment) -> dict:
    user_ext = str(enr.user.external_id)
    rg = documents_iface.get_rg(user_ext)
    p = profiles.get(enr.user)
    fields = {
        "number": rg.number if rg else None,
        "issuing_agency": rg.issuing_agency if rg else None,
        "issue_date": rg.issue_date.isoformat() if (rg and rg.issue_date) else None,
        "mother_name": p.mother_name if p else None,
        "father_name": p.father_name if p else None,
        "birthplace": p.birthplace if p else None,
        "marital_status": p.marital_status if p else None,
        "nationality": p.nationality if p else None,
    }
    result = (rg.validation_result or {}) if rg else {}
    # MESMA régua da selfie (_selfie_dict: `enr.selfie_status if enr.selfie_image else None`):
    # sem foto não há análise em voo, então `analysis_status` é None (front mostra o upload, não
    # "extraindo…"). O RG nasce com validation_status="pending" por default — surfá-lo cru travava
    # o passo RG num spinner eterno mesmo sem nenhuma foto enviada.
    has_photo = bool(rg and (rg.front_photo or rg.back_photo or rg.full_photo))
    return {
        **fields,
        "name": p.name if p else None,
        "birth_date": p.birth_date.isoformat() if (p and p.birth_date) else None,
        "front_photo": rg.front_photo if rg else None,
        "back_photo": rg.back_photo if rg else None,
        "full_photo": rg.full_photo if rg else None,
        # canônico unificado (proposta #4) + alias `validation_*` (compat) até o front migrar.
        "analysis_status": rg.validation_status if has_photo else None,
        "analysis_reason": result.get("reason"),
        "validation_status": rg.validation_status if has_photo else None,
        "validation_reason": result.get("reason"),
        "missing_fields": [
            k for k in (*_RG_DOC_FIELDS, *_RG_PROFILE_FIELDS) if not fields[k]
        ],
    }


def get_rg_section(*, user_external_id: str) -> dict:
    """GET da seção documento (plan/13): fotos + validação + TODOS os campos (extraídos pela IA
    ou digitados) + `missing_fields` (o que o aluno ainda precisa completar)."""
    enr = _require(user_external_id)
    _reconcile_stale_analyses(enr)  # TTL guard (proposta #2)
    return _rg_section_dict(enr)


def patch_rg_section(*, user_external_id: str, **fields) -> dict:
    """PATCH da seção documento (plan/13): completa/CORRIGE o que a extração não trouxe — campos
    do doc e do perfil. Aceito em qualquer etapa da coleta (é dado do aluno, não progressão);
    a foto do documento segue sendo a fonte de verdade pra auditoria do coordenador."""
    enr = _require(user_external_id, _S.RG, _S.ADDRESS, _S.EDUCATION, _S.SELFIE)
    doc_payload = {k: fields[k] for k in _RG_DOC_FIELDS if fields.get(k) is not None}
    if doc_payload:
        documents_iface.update(user_external_id, {"rg": doc_payload})
    profile_payload = {
        k: fields[k] for k in _RG_PROFILE_FIELDS if fields.get(k) is not None
    }
    if profile_payload:
        profiles.update_identity(
            enr.user, **profile_payload
        )  # identidade → Profile (correção)
    _advance_rg(enr, user_external_id)
    # destrave do gate #10: selfie já aprovada que só esperava os campos → fecha a coleta agora
    from users.roles import _selfie

    if enr.status == _S.SELFIE and enr.selfie_status == _selfie.APPROVED:
        _advance_to_release(enr)
    return me_dict(
        enr
    )  # resposta canônica (proposta #3): o rg detalhado segue no GET da seção


def upload_rg_photo(*, user_external_id: str, slot: str, upload) -> dict:
    """Foto do RG (slot `rg_front`/`rg_back`/`rg_full`), dentro da seção `rg` — plan/12.

    Salva (PDF vira JPEG no `documents`), re-zera a validação e ENFILEIRA o pipeline de IA
    (visão → OCR → extração → biometria). O upload responde na hora; o veredito (e o motivo,
    se reprovar) sai pelo `/enrollment/me`. Aluno: RG é obrigatório (Victor)."""
    from users.roles import _analysis

    enr = _require(user_external_id, _S.RG)
    path = documents_iface.upload_photo(user_external_id, slot, upload)
    _reset_rg_validation(user_external_id, slot)
    from django_q.tasks import async_task

    async_task("users.roles.enrollment.tasks.validate_rg", enr.id, slot)
    # ack de polling (proposta #2): a análise acabou de (re)começar → started_at = agora.
    rg = documents_iface.get_rg(user_external_id)
    return {"stored": path, **_analysis.ack(_analysis.PENDING, _rg_started_at(rg))}


def selfie_ack(enr: Enrollment) -> dict:
    """Ack de polling da selfie (proposta #2) — pro POST devolver junto com o estado."""
    from users.roles import _analysis

    return _analysis.ack(enr.selfie_status, enr.selfie_taken_at)


def _advance_rg(enr: Enrollment, user_external_id: str) -> None:
    """Avança RG→ADDRESS (ordem plan/13) quando a seção fecha: validação IA APROVADA (frente+verso
    OU inteira) + `number` presente (extraído pelo OCR ou digitado no PATCH). Com chain-skip."""
    from users.roles import _document_ai as doc_ai

    if enr.status != _S.RG:
        return
    rg = documents_iface.get_rg(user_external_id)
    if rg is not None and rg.validation_status == doc_ai.APPROVED and rg.number:
        _advance_to(enr, _S.ADDRESS)


# ── validação do RG por IA (plan/12): visão → OCR → extração → biometria ────
# Roda na task Django-Q (`tasks.validate_rg`); aqui é a orquestração (status no RG, notifies,
# avanço do wizard). As chamadas de IA moram em `users/roles/_document_ai.py` (compartilhável).

_RG_SLOT_FIELD = {
    "rg_front": "front_photo",
    "rg_back": "back_photo",
    "rg_full": "full_photo",
}
_RG_SLOT_SIDE = {"rg_front": "front", "rg_back": "back", "rg_full": "full"}
_MIME_BY_EXT = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
}


def _reset_rg_validation(user_external_id: str, slot: str) -> None:
    """Re-upload de um slot re-zera o veredito daquela foto + a extração (re-analisa tudo)."""
    from users.roles import _document_ai as doc_ai

    rg = documents_iface.get_rg(user_external_id)
    if rg is None:
        return
    from django.utils import timezone

    result = rg.validation_result or {}
    photos = dict(result.get("photos") or {})
    photos.pop(slot, None)
    for key in ("extracted", "name_match", "reason", "human"):
        result.pop(key, None)
    result["photos"] = photos
    # marca o INÍCIO da análise (proposta #2): o re-upload reinicia o relógio do TTL. Guardado no
    # JSON (sem migração); só vale enquanto `pending` (o `_finish_rg` reescreve o result ao concluir).
    result["analysis_started_at"] = timezone.now().isoformat()
    rg.validation_status = doc_ai.PENDING
    rg.validation_result = result
    rg.validated_at = None
    rg.save(update_fields=["validation_status", "validation_result", "validated_at"])


def run_rg_validation(enrollment_id: int, slot: str) -> None:
    """Pipeline da task (plan/12). Idempotente: só age com validação `pending`.

    a) visão na foto do `slot` (é RG? lado certo? legível?) → reprovou/dúvida = para e notifica;
    b) seção completa (inteira aprovada OU frente+verso aprovadas) → OCR + extração (1 LLM);
    c) nome de outra pessoa → reprova; dúvida → review; ok → povoa campos VAZIOS →
       biometria → avança o wizard."""
    from pathlib import Path

    from users.roles import _document_ai as doc_ai

    enr = (
        Enrollment.objects.select_related("user", "hub", "hub__coordinator")
        .filter(id=enrollment_id)
        .first()
    )
    if enr is None:
        return
    user_ext = str(enr.user.external_id)
    rg = documents_iface.get_rg(user_ext)
    if rg is None or rg.validation_status != doc_ai.PENDING:
        return

    result = rg.validation_result or {}
    photos = dict(result.get("photos") or {})

    field = _RG_SLOT_FIELD.get(slot)
    path = getattr(rg, field, None) if field else None
    if path and (photos.get(slot) or {}).get("status") != doc_ai.APPROVED:
        fp = Path(settings.MEDIA_ROOT) / path
        if not fp.exists():
            return
        mime = _MIME_BY_EXT.get(fp.suffix.lstrip(".").lower(), "image/jpeg")
        # pré-tratamento (Victor 2026-06-11): endireita a foto ANTES de validar — EXIF + auto-rotação
        # por IA. Melhora visão/OCR/biometria e deixa o documento guardado reto. Best-effort.
        doc_ai.fix_orientation(str(fp), mime_type=mime, caller="enrollment.rg")
        status, reason = doc_ai.check_photo(
            fp.read_bytes(),
            side=_RG_SLOT_SIDE[slot],
            mime_type=mime,
            caller="enrollment.rg",
        )
        # merge FRESCO: a visão leva 10–60s e frente+verso viram 2 tasks em workers paralelos —
        # re-lê antes de gravar pra não perder o veredito que o outro worker salvou no meio tempo.
        rg.refresh_from_db()
        if rg.validation_status != doc_ai.PENDING:
            return  # outro worker (ou re-upload) já mudou o estado — não sobrescrever
        result = rg.validation_result or {}
        photos = dict(result.get("photos") or {})
        photos[slot] = {"status": status, "reason": reason}
        result["photos"] = photos
        if status != doc_ai.APPROVED:
            _finish_rg(enr, rg, status, reason, result)
            return

    images = _rg_approved_images(rg, photos)
    if images is None:
        # esta foto passou, mas falta a outra — guarda o veredito e espera o resto da seção
        rg.validation_result = result
        rg.save(update_fields=["validation_result"])
        return
    _rg_extract_and_finish(enr, rg, result, images)


def _rg_approved_images(rg, photos: dict) -> list | None:
    """Imagens da seção completa e aprovada (inteira OU frente+verso), ou None se ainda falta."""
    from pathlib import Path

    from users.roles import _document_ai as doc_ai

    def ok(slot: str) -> bool:
        return (photos.get(slot) or {}).get("status") == doc_ai.APPROVED

    if rg.full_photo and ok("rg_full"):
        return [Path(settings.MEDIA_ROOT) / rg.full_photo]
    if rg.front_photo and rg.back_photo and ok("rg_front") and ok("rg_back"):
        return [
            Path(settings.MEDIA_ROOT) / rg.front_photo,
            Path(settings.MEDIA_ROOT) / rg.back_photo,
        ]
    return None


def _rg_extract_and_finish(enr: Enrollment, rg, result: dict, images: list) -> None:
    """OCR + extração (1 LLM): confere o nome (tolerância de casamento) e povoa os campos."""
    from users.roles import _document_ai as doc_ai

    p = profiles.get(enr.user)
    try:
        ocr_text = doc_ai.ocr_images(
            [fp.read_bytes() for fp in images], caller="enrollment.rg"
        )
        data = doc_ai.extract_rg(
            ocr_text, holder_name=(p.name if p else None), caller="enrollment.rg"
        )
    except Exception as exc:  # noqa: BLE001 — IA fora do ar na extração → review (humano decide)
        logger.warning(
            "enrollment.rg_extract_failed",
            enrollment=str(enr.external_id),
            error=str(exc)[:200],
        )
        _finish_rg(
            enr,
            rg,
            doc_ai.REVIEW,
            "IA indisponível na extração dos dados — enviado para revisão manual do coordenador.",
            result,
        )
        return
    # guard do worker-zumbi (Victor 2026-06-17): o OCR + extração acima levam ~15s; se NESSE meio o
    # sweep do TTL (worker que ficou lento) ou o coordenador já decidiu, NÃO sobrescrever a decisão.
    # Mesma régua que a visão já aplica nas linhas 543-545 — aqui fecha a janela do estágio de extração.
    rg.refresh_from_db()
    if rg.validation_status != doc_ai.PENDING:
        return
    result["extracted"] = data
    match = str(data.get("name_match") or "").strip().lower()
    name_reason = (data.get("name_reason") or "").strip()
    if match in ("nao", "não", "no"):
        _finish_rg(
            enr,
            rg,
            doc_ai.REJECTED,
            f"O nome no documento não confere com o do cadastro. {name_reason}".strip(),
            result,
        )
        return
    if match not in ("sim", "yes"):
        _finish_rg(
            enr,
            rg,
            doc_ai.REVIEW,
            f"Não deu pra confirmar o nome do titular. {name_reason}".strip(),
            result,
        )
        return
    _apply_rg_extracted(enr, rg, data)
    _finish_rg(enr, rg, doc_ai.APPROVED, name_reason or "Documento validado.", result)
    _notify_rg_approved(enr)  # notify também no aprovado automático (plan/13)
    _rg_post_approval(enr, rg)


def _apply_rg_extracted(enr: Enrollment, rg, data: dict) -> None:
    """Povoa SÓ campos vazios (Victor: não sobrescrever): doc RG + perfil da matrícula + nascimento."""
    from datetime import date

    def _clean(value, limit: int):
        s = str(value).strip()
        return s[:limit] if s else None

    def _date(value):
        try:
            return date.fromisoformat(str(value)) if value else None
        except ValueError:
            return None

    changed = []
    if not rg.number and data.get("number"):
        rg.number = _clean(data["number"], 30)
        changed.append("number")
    if not rg.issuing_agency and data.get("issuing_agency"):
        rg.issuing_agency = _clean(data["issuing_agency"], 50)
        changed.append("issuing_agency")
    if not rg.issue_date:
        d = _date(data.get("issue_date"))
        if d:
            rg.issue_date = d
            changed.append("issue_date")
    if changed:
        rg.save(update_fields=changed)

    # filiação/naturalidade + nascimento extraídos do documento → CENTRALIZADO no Profile (Victor
    # 2026-06-16: a identidade mora SÓ no Profile, nunca espalhada no enrollment).
    profiles.fill_identity(
        enr.user,
        mother_name=_clean(data["mother_name"], 255)
        if data.get("mother_name")
        else None,
        father_name=_clean(data["father_name"], 255)
        if data.get("father_name")
        else None,
        birthplace=_clean(data["birthplace"], 128) if data.get("birthplace") else None,
        birth_date=_date(data.get("birth_date")),
    )


def _finish_rg(
    enr: Enrollment, rg, status: str, reason: str | None, result: dict
) -> None:
    """Grava o veredito (justificativa SEMPRE — plan/9) + dispara o notify do estado."""
    from django.utils import timezone

    from users.roles import _document_ai as doc_ai

    result["reason"] = reason
    rg.validation_status = status
    rg.validation_result = result
    rg.validated_at = timezone.now()
    rg.save(update_fields=["validation_status", "validation_result", "validated_at"])
    logger.info(
        "enrollment.rg_validated", enrollment=str(enr.external_id), status=status
    )
    if status == doc_ai.REJECTED:
        _notify_rg_rejected(enr, reason)
    elif status == doc_ai.REVIEW:
        _notify_rg_review(enr, reason)


def _rg_post_approval(enr: Enrollment, rg) -> None:
    """Aprovado → AVANÇA o wizard PRIMEIRO, biometria best-effort DEPOIS: um crash da biometria
    (InsightFace/onnxruntime pode matar o worker) NÃO pode perder o avanço (Victor 2026-06-16).

    Recorte (plan/13): InsightFace direto (já detecta/recorta); NÃO achou rosto → a visão
    localiza a região da foto do titular, o Pillow recorta e tenta de novo. Nunca trava o fluxo."""
    # o doc já está aprovado → avança rg→address ANTES de tocar na biometria.
    _advance_rg(enr, str(enr.user.external_id))

    from pathlib import Path

    from integrations.tools.biometric import service as biometric

    from users.roles import _document_ai as doc_ai

    face_path = rg.front_photo or rg.full_photo
    face_slot = "rg_front" if rg.front_photo else "rg_full"
    if face_path:
        full = Path(settings.MEDIA_ROOT) / face_path
        enrolled = biometric.try_enroll_document(
            user=enr.user,
            slot=face_slot,
            image_path=str(full),
            caller="enrollment.document",
        )
        if enrolled is None and full.exists():
            cropped = doc_ai.crop_face(full.read_bytes(), caller="enrollment.rg")
            if cropped:
                from core.media import save_media

                crop_rel = save_media(prefix="documents", data=cropped, ext="jpg")
                biometric.try_enroll_document(
                    user=enr.user,
                    slot="rg_front",
                    image_path=str(Path(settings.MEDIA_ROOT) / crop_rel),
                    caller="enrollment.document_crop",
                )


def run_rg_fill(enrollment_id: int) -> None:
    """Pós-aprovação do coordenador: OCR+extração best-effort SÓ pra preencher campos vazios.

    A aprovação humana é FINAL — aqui não há veto (o `name_match` fica só registrado). Falhou
    a IA → o aluno digita o que faltou (`missing_fields` no /me)."""
    from pathlib import Path

    from users.roles import _document_ai as doc_ai

    enr = (
        Enrollment.objects.select_related("user", "hub")
        .filter(id=enrollment_id)
        .first()
    )
    if enr is None:
        return
    user_ext = str(enr.user.external_id)
    rg = documents_iface.get_rg(user_ext)
    if rg is None or rg.validation_status != doc_ai.APPROVED:
        return
    result = rg.validation_result or {}
    if result.get("extracted"):
        return
    images = [
        Path(settings.MEDIA_ROOT) / p
        for p in ([rg.full_photo] if rg.full_photo else [rg.front_photo, rg.back_photo])
        if p
    ]
    images = [fp for fp in images if fp.exists()]
    if not images:
        return
    p = profiles.get(enr.user)
    try:
        ocr_text = doc_ai.ocr_images(
            [fp.read_bytes() for fp in images], caller="enrollment.rg_fill"
        )
        data = doc_ai.extract_rg(
            ocr_text, holder_name=(p.name if p else None), caller="enrollment.rg_fill"
        )
    except Exception as exc:  # noqa: BLE001 — best-effort: falhou → o aluno digita
        logger.warning(
            "enrollment.rg_fill_failed",
            enrollment=str(enr.external_id),
            error=str(exc)[:200],
        )
        return
    result["extracted"] = data
    rg.validation_result = result
    rg.save(update_fields=["validation_result"])
    _apply_rg_extracted(enr, rg, data)
    _advance_rg(enr, user_ext)


def decide_rg(
    *,
    enrollment_external_id: str,
    coordinator,
    approve: bool,
    reason: str | None = None,
) -> dict:
    """Coordenador do hub decide o RG em REVISÃO (sim/não). A decisão humana é FINAL sobre a
    validade: aprovou → avisa o aluno + biometria + extração best-effort preenche os campos
    (sem veto); reprovou → volta pro aluno refazer (com o motivo)."""
    from users.roles import _document_ai as doc_ai

    enr = _enrollment_for_coordinator(enrollment_external_id, coordinator)
    rg = documents_iface.get_rg(str(enr.user.external_id))
    if rg is None or rg.validation_status != doc_ai.REVIEW:
        raise EnrollmentError(
            "O RG não está em revisão.",
            code="RG_NOT_IN_REVIEW",
            extra={"rg_validation_status": rg.validation_status if rg else "missing"},
        )
    note = (reason or "").strip() or (
        "aprovado pelo coordenador" if approve else "reprovado pelo coordenador"
    )
    result = rg.validation_result or {}
    result["human"] = {
        "approve": approve,
        "reason": note,
        "by": str(coordinator.external_id),
    }
    if not approve:
        _finish_rg(enr, rg, doc_ai.REJECTED, note, result)
        return {
            "external_id": str(enr.external_id),
            "status": enr.status,
            "rg_validation_status": rg.validation_status,
        }
    # aprovação humana: as fotos presentes valem como aprovadas (fica registrado por foto)
    photos = dict(result.get("photos") or {})
    for slot, field in _RG_SLOT_FIELD.items():
        if getattr(rg, field, None):
            photos[slot] = {"status": doc_ai.APPROVED, "reason": note}
    result["photos"] = photos
    _finish_rg(enr, rg, doc_ai.APPROVED, note, result)
    _notify_rg_approved(enr)
    if result.get("extracted"):
        # a revisão veio da dúvida de NOME — extração já existe, povoa agora
        _apply_rg_extracted(enr, rg, result["extracted"])
    else:
        # a revisão veio da visão/IA fora do ar — extração roda best-effort em 2º plano
        from django_q.tasks import async_task

        async_task("users.roles.enrollment.tasks.fill_rg_data", enr.id)
    _rg_post_approval(enr, rg)
    return {
        "external_id": str(enr.external_id),
        "status": enr.status,
        "rg_validation_status": rg.validation_status,
    }


def _resume_link() -> str:
    """Deep-link de re-entrada no wizard (proposta #11): FRONTEND_URL + ENROLLMENT_RESUME_PATH.
    Sem front configurado → vazio (a mensagem sai sem o link)."""
    from users.roles.lead.config import frontend_url

    base = frontend_url().rstrip("/")
    if not base:
        return ""
    return base + getattr(settings, "ENROLLMENT_RESUME_PATH", "/matricula")


_SELFIE_STORY_PROMPT = (
    "Você é um redator de mensagens que EMOCIONAM. Escreve para uma pessoa que acabou de assinar, com o "
    "próprio rosto, a matrícula num supletivo (EJA) — ela voltou a estudar. Já pagou, já enviou tudo: a "
    "matrícula está confirmada. Esta mensagem é pra marcar essa decisão na vida dela.\n\n"
    "Escreva uma mensagem curta (8 a 12 linhas) que:\n"
    "- Fale com a PESSOA, não sobre o produto. O herói da mensagem é a coragem dela de voltar a estudar.\n"
    "- EMOCIONE de verdade: toque no peso de recomeçar depois de anos, no que essa decisão significa, em "
    "quem ela honra ao dar esse passo. Pode ser forte, pode dar um nó na garganta — desde que seja "
    "VERDADEIRO, nunca água com açúcar.\n"
    "- Reconheça que dar esse passo JÁ é uma vitória, independente do que venha depois.\n"
    "- Fuja de clichê vazio e bajulação genérica ('você é incrível', 'extraordinário', 'você consegue'): "
    "emoção se constrói com verdade concreta, não com elogio solto.\n"
    "- NÃO mande a pessoa aguardar, esperar ser chamada, nem explique trâmite burocrático ('a secretaria "
    "vai analisar', 'você será chamada em breve'). Isso esfria tudo. Encerre olhando pra FRENTE — o que "
    "se abre a partir daqui — não pra fila de espera.\n"
    "- NÃO invente detalhes sobre a vida, dificuldades ou histórico da pessoa além do fornecido.\n"
    "- NÃO venda nada nem peça indicações.\n"
    "- Trate pelo nome (use mais de uma vez) e ajuste querida/querido/neutro conforme o sexo, sem soar "
    "forçado.\n\n"
    "Adapte pela idade: jovem (até ~20), o futuro que se abre, a vida inteira pela frente; adulto, o "
    "mérito raro de voltar a estudar no meio do trabalho, da família, da correria.\n\n"
    "A aparência física abaixo é REAL (extraída da foto) e serve SÓ pra você calibrar o tom (cansaço, "
    "horário, ambiente). NUNCA descreva nem comente o rosto, o corpo ou a aparência da pessoa na "
    "mensagem — isso constrange.\n\n"
    "Dados do aluno:\n"
    "- Nome: {name}\n"
    "- Idade: {idade}\n"
    "- Sexo: {sexo}\n"
    "- Aparência física (foto): {aparencia}\n"
    "- Observação: (não informada)\n\n"
    "Escreva a mensagem final, pronta para envio, sem comentários extras antes ou depois do texto."
)


def _selfie_story(enr: Enrollment, p, first: str) -> str | None:
    """Storytelling RICO da matrícula assinada (Victor 2026-06-21): a visão (MiniMax) descreve a selfie
    só pra CALIBRAR o tom; o Opus 4.8 escreve um pequeno discurso motivacional (fallback DeepSeek →
    MiniMax). Dados REAIS (idade/sexo). Best-effort: qualquer falha devolve None e o caller cai no teor
    fixo. Roda async (qcluster), então a latência da visão+LLM não trava ninguém."""
    from pathlib import Path

    from integrations.ai import service as ai
    from users.roles import notifications as msgs

    try:
        desc = ""
        try:
            if enr.selfie_image:
                fp = Path(settings.MEDIA_ROOT) / enr.selfie_image
                if fp.exists():
                    desc = ai.describe_image(
                        fp.read_bytes(),
                        caller="story.selfie.vision",
                        prompt=(
                            "Descreva objetivamente a aparencia da pessoa nesta selfie em 4-6 linhas "
                            "(idade aparente, expressao, vestimenta, ambiente, iluminacao, mood/horario). "
                            "Nao identifique quem e."
                        ),
                    )
        except Exception:  # noqa: BLE001 — visão é apoio; sem ela o tom é genérico
            desc = ""

        idade = msgs.age_from(getattr(p, "birth_date", None))
        sexo = {"M": "Masculino", "F": "Feminino"}.get(
            (getattr(p, "gender", None) or "").upper(), "não informado"
        )
        instruction = _SELFIE_STORY_PROMPT.format(
            name=first,
            idade=f"{idade} anos" if idade else "não informada",
            sexo=sexo,
            aparencia=desc or "não disponível",
        )
        for mdl in ("claude-opus-4-8", "deepseek-v4-pro", "MiniMax-M3"):
            try:
                out = ai.generate_text(
                    f"Escreva a mensagem para {first}.",
                    caller=f"story.selfie.{mdl}",
                    instruction=instruction,
                    temperature=0.7,
                    max_tokens=900,
                    model=mdl,
                )
                out = (out or "").strip().replace("**", "")
                if len(out) >= 60 and first.lower() in out.lower():
                    return out
            except Exception:  # noqa: BLE001 — falhou esse modelo, tenta o próximo
                continue
        return None
    except Exception as exc:  # noqa: BLE001 — jamais quebra o notify
        logger.warning("enrollment.selfie_story_failed", error=str(exc))
        return None


def _notify_resolution(enr: Enrollment, event_key: str, **placeholders) -> None:
    """Notify de RESOLUÇÃO de análise pro ALUNO (aprovado/reprovado — automático OU decisão do
    coordenador): **multicanal** (WhatsApp + e-mail) com **deep-link** de volta pro wizard
    (proposta #11) — o aluno não precisa segurar a tela pollando. Best-effort, nunca quebra o fluxo."""
    from notify.interface.send import send
    from users.roles import notifications as msgs

    p = profiles.get(enr.user)
    name = msgs.first_name(p.name if p else None)
    fallback = msgs.text(event_key, name=name, **placeholders)
    if event_key == "enrollment.selfie_approved":
        # assinatura da matrícula = pequeno discurso motivacional RICO (visão da selfie → Opus 4.8,
        # fallback DeepSeek → MiniMax). Cai no story_text simples e, por fim, no teor fixo.
        text = _selfie_story(enr, p, name) or msgs.story_text(
            event_key,
            name=name,
            fallback=fallback,
            age=msgs.age_from(getattr(p, "birth_date", None)),
        )
    else:
        text = msgs.story_text(
            event_key,
            name=name,
            fallback=fallback,
            age=msgs.age_from(getattr(p, "birth_date", None)),
        )
    # O link de "continuar" só faz sentido quando o aluno PRECISA voltar ao wizard. Em marcos de
    # conclusão/assinatura (a mensagem manda AGUARDAR) o link se contradiz — por isso o guard.
    link = _resume_link()
    if link and event_key not in {"enrollment.selfie_approved"}:
        text += f"\n\nContinue sua matrícula por aqui: {link}"
    tts = msgs.is_tts(event_key)
    try:
        send(
            text=text,
            caller=event_key,
            phone=p.phone if p else None,
            email=p.email if p else None,
            email_channel=bool(p and p.email),
            subject="Sua matrícula — atualização",
            tts=tts,
            gender=p.gender if (p and tts) else None,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "enrollment.notify_resolution_failed", event=event_key, error=str(exc)
        )


def _notify_rg_rejected(enr: Enrollment, reason: str | None) -> None:
    _notify_resolution(enr, "enrollment.rg_rejected", detail=(reason or "").strip())


def _notify_rg_review(enr: Enrollment, reason: str | None) -> None:
    from notify.interface.send import send

    from users.roles import notifications as msgs

    coord = enr.hub.coordinator
    if coord is None:
        return
    cp = profiles.get(coord)
    try:
        send(
            text=msgs.text(
                "enrollment.rg_in_review",
                name=msgs.first_name(cp.name if cp else None),
                detail=(reason or "").strip(),
            ),
            caller="enrollment.rg_in_review",
            phone=cp.phone if cp else None,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("enrollment.notify_rg_review_failed", error=str(exc))


def _notify_rg_approved(enr: Enrollment) -> None:
    _notify_resolution(enr, "enrollment.rg_approved")


def get_education(*, user_external_id: str) -> dict:
    """GET dos dados educacionais (plan/13). Tudo None = ainda não preenchido."""
    enr = _require(user_external_id)
    try:
        edu = enr.educational_data
    except EducationalData.DoesNotExist:
        edu = None
    return {
        "level": edu.level if edu else None,
        "grade": edu.grade if edu else None,
        "completed": edu.completed if edu else None,
        "last_school": edu.last_school if edu else None,
        "city": edu.city if edu else None,
        "state": edu.state if edu else None,
        "last_year_when": edu.last_year_when if edu else None,
    }


def set_education(
    *,
    user_external_id: str,
    level: str,
    grade: int,
    completed: bool,
    last_school: str,
    city: str,
    state: str,
    last_year_when=None,
) -> Enrollment:
    enr = _require(user_external_id, _S.EDUCATION)
    # nível válido (Fundamental | Médio)
    if level not in EducationalData.Level.values:
        raise EnrollmentError(
            "Nível de ensino inválido.",
            code="EDUCATION_LEVEL_INVALID",
            extra={"level": level, "allowed": list(EducationalData.Level.values)},
        )
    # série dentro da faixa do nível: Fundamental 1–9, Médio 1–3 (o dado mais valioso da matrícula)
    lo, hi = EducationalData.GRADE_RANGE[level]
    if grade is None or not (lo <= grade <= hi):
        raise EnrollmentError(
            f"Série fora da faixa do nível ({lo}–{hi}).",
            code="EDUCATION_GRADE_OUT_OF_RANGE",
            extra={"level": level, "grade": grade, "min": lo, "max": hi},
        )
    EducationalData.objects.update_or_create(
        enrollment=enr,
        defaults={
            "level": level,
            "grade": grade,
            "completed": completed,
            "last_school": last_school,
            "city": city,
            "state": state,
            "last_year_when": last_year_when,
        },
    )
    _set_status(enr, _S.SELFIE)
    return enr


# Mensagem PÚBLICA da selfie (o que o aluno vê). O comentário cru da IA (`selfie_description`) é
# INTERNO/auditoria e NUNCA é exposto ao front (Victor 2026-06-21) — o `status` diz o resto.
_SELFIE_PUBLIC_REASON = {
    "rejected": "Não conseguimos confirmar sua selfie. Envie uma nova foto, nítida e com o rosto bem visível.",
    "review": "Recebemos sua selfie e estamos confirmando. Avisamos você em instantes.",
}


def _selfie_dict(enr: Enrollment) -> dict:
    """Bloco da selfie (GET /selfie e o bloco `selfie` do /me — proposta #3)."""
    from users.roles import _analysis

    status = enr.selfie_status if enr.selfie_image else None
    return {
        "exists": bool(enr.selfie_image),
        "photo": enr.selfie_image,
        "taken_at": enr.selfie_taken_at.isoformat() if enr.selfie_taken_at else None,
        "status": status,
        # canônico unificado (proposta #4): `analysis_status`/`analysis_reason` + alias `status`/
        # `description` (compat). `expires_at` = até quando o `pending` vale (proposta #2).
        "analysis_status": status,
        "analysis_reason": _SELFIE_PUBLIC_REASON.get(status),
        "expires_at": (
            _analysis.expires_at(enr.selfie_taken_at).isoformat()
            if status == _analysis.PENDING and enr.selfie_taken_at
            else None
        ),
        "verified": enr.selfie_verified,
        "description": _SELFIE_PUBLIC_REASON.get(status),
    }


def get_selfie(*, user_external_id: str) -> dict:
    """GET da selfie/ASSINATURA (plan/13): foto, quando foi enviada, status e os comentários da
    IA/biometria (inclusive as instruções de como ser aprovada). `exists: false` = não enviada."""
    enr = _require(user_external_id)
    _reconcile_stale_analyses(enr)  # TTL guard (proposta #2)
    return _selfie_dict(enr)


def set_selfie(
    *, user_external_id: str, image_bytes: bytes, content_type: str = "image/jpeg"
) -> Enrollment:
    """Selfie = a ASSINATURA da matrícula (plan/13). Salva a foto e ENFILEIRA a análise: IA
    pré-analisa (vale ir pra biometria?) → face-match vs rosto do DOCUMENTO → reprovou? a IA
    INSTRUI como ser aprovada. Responde na hora; o front acompanha pelo GET /selfie (status)."""
    from django.utils import timezone

    from users.roles import _selfie

    enr = _require(user_external_id, _S.SELFIE)
    _require_rg_ready_for_selfie(enr)  # gates #9/#10: rosto do doc + perfil completo
    enr.selfie_image = _save_selfie(enr, image_bytes, content_type)
    enr.selfie_taken_at = timezone.now()
    enr.selfie_status = _selfie.SelfieStatus.PENDING
    enr.selfie_verified = False
    enr.selfie_description = None
    enr.save()
    from django_q.tasks import async_task

    async_task("users.roles.enrollment.tasks.validate_selfie", enr.id)
    return enr


def _require_rg_ready_for_selfie(enr: Enrollment) -> None:
    """Gates da selfie (propostas #9/#10), 409 `WRONG_STATUS` + `expected_status:"rg"`:

    - **#9**: a biometria compara a selfie com o ROSTO do documento → exige frente OU inteira
      APROVADA (o wizard normalmente garante; o buraco é a aprovação HUMANA com só o verso).
    - **#10**: a matrícula não pode fechar sem estado civil/nacionalidade (campos que o RG não
      traz) → exige `missing_fields` vazio; o front roteia de volta pro PATCH rg."""
    from users.roles import _document_ai as doc_ai

    rg = documents_iface.get_rg(str(enr.user.external_id))
    if (
        rg is None
        or rg.validation_status != doc_ai.APPROVED
        or not (rg.front_photo or rg.full_photo)
    ):
        raise Conflict(
            "Envie a frente do RG antes da selfie.",
            code="WRONG_STATUS",
            extra={"expected_status": _S.RG.value},
        )
    missing = _rg_section_dict(enr)["missing_fields"]
    if missing:
        raise Conflict(
            "Complete os dados do documento antes da selfie: "
            + ", ".join(missing)
            + ".",
            code="WRONG_STATUS",
            extra={"expected_status": _S.RG.value, "missing_fields": missing},
        )


def run_selfie_validation(enrollment_id: int) -> None:
    """Pipeline da task da selfie (plan/13). Idempotente: só age com `selfie_status` pending.

    a) liveness (é selfie real? adianta ir pra biometria?) → b) face-match vs rosto do DOCUMENTO
    (biometria do RG) → c) reprovou? a visão olha DE NOVO e gera INSTRUÇÕES práticas de como ser
    aprovada (vão no GET e no notify) → d) notifies: aprovada→aluno; reprovada→aluno; review→coord."""
    from pathlib import Path

    from users.roles import _selfie

    enr = (
        Enrollment.objects.select_related("user", "hub", "hub__coordinator")
        .filter(id=enrollment_id)
        .first()
    )
    if enr is None or not enr.selfie_image or enr.status != _S.SELFIE:
        return
    if enr.selfie_status != _selfie.SelfieStatus.PENDING:
        return
    fp = Path(settings.MEDIA_ROOT) / enr.selfie_image
    if not fp.exists():
        return
    content_type = _MIME_BY_EXT.get(fp.suffix.lstrip(".").lower(), "image/jpeg")
    image_bytes = fp.read_bytes()
    status, desc = _selfie.verify(image_bytes, content_type, caller="enrollment.selfie")
    # SOMAR (Victor 2026-06-05): face-match biométrico selfie × documento. Avança só se os dois passarem.
    status, desc = _selfie.add_face_match(
        user=enr.user,
        selfie_image_path=str(fp),
        caller="enrollment.selfie",
        liveness_status=status,
        liveness_desc=desc,
    )
    if status == _selfie.REJECTED:
        tips = _selfie.instructions(
            image_bytes, content_type, reason=desc, caller="enrollment.selfie"
        )
        if tips:
            desc = f"{desc}\n\nComo resolver: {tips}"
    enr.refresh_from_db(fields=["selfie_status"])
    if enr.selfie_status != _selfie.SelfieStatus.PENDING:
        return  # re-upload no meio tempo — este veredito é de foto velha, descarta
    enr.selfie_status = status
    enr.selfie_verified = status == _selfie.APPROVED
    enr.selfie_description = desc
    enr.save(
        update_fields=[
            "selfie_status",
            "selfie_verified",
            "selfie_description",
            "updated_at",
        ]
    )
    logger.info(
        "enrollment.selfie_validated", enrollment=str(enr.external_id), status=status
    )
    _save_selfie_audit(enr, status, desc)
    _resolve_selfie(enr)


def _resolve_selfie(enr: Enrollment) -> None:
    """Reage ao veredito: aprovada→avisa o aluno + aguarda liberação; reprovada→avisa o aluno
    (com as instruções); revisão→avisa o coordenador."""
    from users.roles import _selfie

    if enr.selfie_status == _selfie.APPROVED:
        _notify_selfie_approved(enr)  # notify também no aprovado (plan/13)
        _advance_to_release(enr)
    elif enr.selfie_status == _selfie.REJECTED:
        _notify_selfie_rejected(enr)
    elif enr.selfie_status == _selfie.REVIEW:
        _notify_selfie_review(enr)


def _advance_to_release(enr: Enrollment) -> None:
    """Selfie aprovada → AWAITING_RELEASE + avisa o coordenador. Idempotente (só sai de SELFIE).

    Gate #10: com `missing_fields` do RG/perfil pendentes (ex.: selfie aprovada pelo COORDENADOR
    enquanto faltava nacionalidade) a matrícula NÃO fecha — fica em SELFIE com a selfie aprovada;
    o `patch_rg_section` destrava quando o aluno completar."""
    if enr.status != _S.SELFIE:
        return
    missing = _rg_section_dict(enr)["missing_fields"]
    if missing:
        logger.info(
            "enrollment.release_blocked_missing_fields",
            enrollment=str(enr.external_id),
            missing=missing,
        )
        return
    _set_status(enr, _S.AWAITING_RELEASE)
    _notify_coordinator_awaiting(enr)


def decide_selfie(
    *,
    enrollment_external_id: str,
    coordinator,
    approve: bool,
    reason: str | None = None,
) -> Enrollment:
    """Coordenador do hub decide a selfie em REVISÃO (sim/não). aprova→aguarda liberação; reprova→refazer."""
    from users.roles import _selfie

    enr = _enrollment_for_coordinator(enrollment_external_id, coordinator)
    if enr.selfie_status != _selfie.REVIEW:
        raise EnrollmentError(
            "A selfie não está em revisão.",
            code="SELFIE_NOT_IN_REVIEW",
            extra={"selfie_status": enr.selfie_status},
        )
    note = (reason or "").strip() or (
        "aprovada pelo coordenador" if approve else "reprovada pelo coordenador"
    )
    enr.selfie_status = _selfie.APPROVED if approve else _selfie.REJECTED
    enr.selfie_verified = approve
    enr.selfie_description = note
    enr.save(
        update_fields=[
            "selfie_status",
            "selfie_verified",
            "selfie_description",
            "updated_at",
        ]
    )
    if approve:
        _notify_selfie_approved(enr)  # notify também no aprovado (plan/13)
        _advance_to_release(enr)
    else:
        _notify_selfie_rejected(enr)
    return enr


def _notify_selfie_rejected(enr: Enrollment) -> None:
    # O comentário da IA (`selfie_description`) é INTERNO (auditoria) — NUNCA vai pro aluno; ele
    # recebe só a mensagem genérica do catálogo (Victor 2026-06-21).
    _notify_resolution(enr, "enrollment.selfie_rejected")


def _notify_selfie_approved(enr: Enrollment) -> None:
    _notify_resolution(enr, "enrollment.selfie_approved")


def _notify_selfie_review(enr: Enrollment) -> None:
    from notify.interface.send import send
    from users.roles import notifications as msgs

    coord = enr.hub.coordinator
    if coord is None:
        return
    cp = profiles.get(coord)
    try:
        send(
            text=msgs.text(
                "enrollment.selfie_in_review",
                name=msgs.first_name(cp.name if cp else None),
            ),
            caller="enrollment.selfie_in_review",
            phone=cp.phone if cp else None,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("enrollment.notify_selfie_review_failed", error=str(exc))


def _save_selfie_audit(enr: Enrollment, status: str, desc: str | None) -> None:
    """Auditoria da selfie (pedido do Victor 2026-06-21): por TENTATIVA, salva os recortes de rosto
    (selfie + documento) + o comentário CRU da IA num diretório com timestamp — nunca sobrescreve, pra
    o time conferir depois se a IA não está 'delirando'. Best-effort: jamais quebra o fluxo da matrícula."""
    try:
        import json
        from datetime import datetime, timezone as _tz
        from pathlib import Path

        from core.media import media_token, save_media_at
        from integrations.tools.biometric import face_match

        # pasta da tentativa por TOKEN aleatório (sem id no path — só o log liga token→enrollment).
        token = media_token()
        base = f"audit/selfie/{token}"

        if enr.selfie_image:
            sp = Path(settings.MEDIA_ROOT) / enr.selfie_image
            if sp.exists():
                crop = face_match.face_crop_bytes(str(sp))
                if crop:
                    save_media_at(path=f"{base}/rosto_selfie.jpg", data=crop)

        rg = documents_iface.get_rg(str(enr.user.external_id))
        doc_rel = (rg.full_photo or rg.front_photo) if rg else None
        if doc_rel:
            dp = Path(settings.MEDIA_ROOT) / doc_rel
            if dp.exists():
                crop = face_match.face_crop_bytes(str(dp))
                if crop:
                    save_media_at(path=f"{base}/rosto_documento.jpg", data=crop)

        save_media_at(
            path=f"{base}/veredito.json",
            data=json.dumps(
                {
                    "quando_utc": datetime.now(_tz.utc).isoformat(),
                    "status": str(status),
                    "comentario_ia": desc or "",
                },
                ensure_ascii=False,
                indent=2,
            ).encode("utf-8"),
        )
        logger.info(
            "enrollment.selfie_audit_saved",
            enrollment=str(enr.external_id),
            user=str(enr.user.external_id),
            path=base,
        )
    except Exception as exc:  # noqa: BLE001 — auditoria nunca quebra o fluxo
        logger.warning("enrollment.selfie_audit_failed", error=str(exc))


def _save_selfie(enr: Enrollment, image_bytes: bytes, content_type: str) -> str:
    from core.media import save_media

    ext = _SELFIE_EXT.get(content_type, "jpg")
    return save_media(prefix="selfie", data=image_bytes, ext=ext)


def _notify_coordinator_awaiting(enr: Enrollment) -> None:
    from notify.interface.send import send
    from users.roles import notifications as msgs

    coord = enr.hub.coordinator
    if coord is None:
        return
    cp = profiles.get(coord)
    try:
        send(
            text=msgs.text(
                "enrollment.awaiting_release",
                name=msgs.first_name(cp.name if cp else None),
            ),
            caller="enrollment.awaiting_release",
            phone=cp.phone if cp else None,
            idempotency_key=f"enr_awaiting_{enr.external_id}",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("enrollment.notify_coord_failed", error=str(exc))


# ── 6c: fase da TAXA do coordenador → conclusão student (plan/14, Victor 2026-06-12) ────────
# A taxa do credenciador é SEMPRE 2 parcelas: a 1ª À VISTA e a 2ª AGENDADA pro vencimento lido de
# DENTRO do QR. Os FATOS (paga/agendada) moram na fila do finance (referência determinística);
# o aluno NUNCA sabe da taxa (política interna — máscara em `public_status`). A CONCLUSÃO exige
# os 2 fatos (independente da ordem) e aí sim promove `enrollment→student` com as credenciais.
# (Substitui o `release` antigo de QRs juntos — descartado pelo Victor: "criado em delírio de IA".)


def _fee_now_ref(enr: Enrollment) -> str:
    """Referência determinística da 1ª parcela (à vista) — idempotência na fila do finance."""
    return f"fee_enr_{enr.external_id}_now"


def _fee_due_ref(enr: Enrollment) -> str:
    """Referência determinística da 2ª parcela (agendada pro vencimento do QR)."""
    return f"fee_enr_{enr.external_id}_due"


def _fee_dict(pr) -> dict | None:
    if pr is None:
        return None
    return {
        "status": pr.status,
        "amount": str(pr.amount),
        "scheduled_for": pr.scheduled_for.isoformat() if pr.scheduled_for else None,
        "paid": pr.status == "paid",
        "last_error": pr.last_error or None,
    }


def fee_facts(enr: Enrollment) -> dict:
    """Situação das 2 parcelas da taxa, lida da fila do finance (visão do COORDENADOR — interna)."""
    from finance.interface import fees

    first = fees.latest_fee_request(_fee_now_ref(enr))
    second = fees.latest_fee_request(_fee_due_ref(enr))
    return {
        "first": _fee_dict(first),
        "second": _fee_dict(second),
        "first_paid": bool(
            first is not None and first.status == fees.PaymentStatus.PAID
        ),
        "second_scheduled": second is not None,
    }


def _enrollment_for_coordinator(
    enrollment_external_id: str, coordinator, *allowed_status
) -> Enrollment:
    """Carrega a matrícula e exige que o `coordinator` coordene o hub dela (gate de TODA ação plan/14)."""
    enr = get_by_external_id(enrollment_external_id)
    if enr is None:
        raise NotFound("Matrícula não encontrada.", code="ENROLLMENT_NOT_FOUND")
    if enr.hub.coordinator_id != coordinator.id:
        raise EnrollmentError(
            "Você não coordena o polo desta matrícula.", code="NOT_HUB_COORDINATOR"
        )
    if allowed_status and enr.status not in allowed_status:
        # visão do coordenador → status REAL (sem máscara).
        raise Conflict(
            "A matrícula está em outra etapa.",
            code="WRONG_STATUS",
            extra={"expected_status": enr.status},
        )
    return enr


def _plan_fee_qr(qr_code: str, amount=None) -> dict:
    """Valida/decodifica o QR no Asaas (read-only, NÃO move dinheiro). QR ruim → erro de domínio."""
    from finance.interface import fees

    if not (qr_code or "").strip():
        raise EnrollmentError("Informe o QR code PIX da taxa.", code="FEE_QR_INVALID")
    try:
        return fees.plan_qr_payment(qr_payload=qr_code, amount=amount)
    except ValueError as exc:
        raise EnrollmentError(
            f"QR code inválido: {exc}", code="FEE_QR_INVALID"
        ) from exc


def _queue_fee(enr: Enrollment, *, qr_code: str, amount, scheduled_for, ref: str):
    """Enfileira (ou REENFILEIRA, se a tentativa anterior falhou) uma parcela na fila do finance."""
    from finance.interface import fees

    existing = fees.latest_fee_request(ref)
    if existing is not None and existing.status == fees.PaymentStatus.FAILED:
        # B.O. na tentativa anterior → o coordenador re-posta (até com QR novo) e a fila re-arma
        # com referência FRESCA (a falhada fica como auditoria; Asaas é idempotente por referência).
        return fees.retry_fee_payment(
            ref, qr_payload=qr_code, amount=amount, scheduled_for=scheduled_for
        )
    return fees.request_fee_payment(
        amount=amount,
        qr_payload=qr_code,
        supplier_name="credenciador",
        scheduled_for=scheduled_for,
        external_reference=ref,
        # relaciona a fee à matrícula (interno; o aluno NÃO sabe da taxa — palavra do Victor).
        source_type=fees.SourceType.ENROLLMENT,
        source_external_id=enr.external_id,
    )


def pay_fee(
    *, enrollment_external_id: str, coordinator, qr_code: str, amount=None
) -> dict:
    """1ª parcela (À VISTA): valida o QR e enfileira o PIX IMEDIATO (mesmo que o QR tenha vencimento —
    à vista é à vista; antecipação já provada real). O status do matriculado NÃO muda aqui: muda quando
    o pagamento CONFIRMAR PAGO (hook `fee.paid` → `fee_paid` — palavra do Victor 2026-06-12).
    Idempotente: repetir o POST não paga 2× (referência determinística `_now`)."""
    enr = _enrollment_for_coordinator(
        enrollment_external_id, coordinator, _S.AWAITING_RELEASE, _S.FEE_SCHEDULED
    )
    if fee_facts(enr)["first_paid"]:
        raise Conflict("A 1ª parcela desta taxa já está paga.", code="FEE_ALREADY_PAID")
    plan = _plan_fee_qr(qr_code, amount)
    pr = _queue_fee(
        enr,
        qr_code=qr_code,
        amount=plan["amount"],
        scheduled_for=None,
        ref=_fee_now_ref(enr),
    )
    logger.info(
        "enrollment.fee_pay_queued",
        external_id=str(enr.external_id),
        amount=str(pr.amount),
    )
    return {
        "external_id": str(enr.external_id),
        "status": enr.status,
        "fees": fee_facts(enr),
    }


def schedule_fee(
    *, enrollment_external_id: str, coordinator, qr_code: str, amount=None
) -> dict:
    """2ª parcela (AGENDADA): o vencimento vem de DENTRO do QR (cobrança com vencimento); QR sem
    vencimento → erro claro (não chuta data). O status muda NO ATO do agendamento → `fee_scheduled`
    (Victor 2026-06-12); QR já vencido → paga imediato (o vencimento chegou — semântica provada).
    NÃO depende da 1ª parcela estar paga — o que depende das duas é a CONCLUSÃO (palavra dele)."""
    enr = _enrollment_for_coordinator(
        enrollment_external_id, coordinator, _S.AWAITING_RELEASE, _S.FEE_PAID
    )
    if fee_facts(enr)["second_scheduled"]:
        raise Conflict(
            "A 2ª parcela desta taxa já está agendada.", code="FEE_ALREADY_SCHEDULED"
        )
    plan = _plan_fee_qr(qr_code, amount)
    if plan["due_date"] is None:
        raise EnrollmentError(
            "Este QR não tem data de vencimento — pra agendar, use o QR da cobrança COM vencimento "
            "(ou pague à vista).",
            code="FEE_QR_NO_DUE_DATE",
        )
    pr = _queue_fee(
        enr,
        qr_code=qr_code,
        amount=plan["amount"],
        scheduled_for=plan["scheduled_for"],
        ref=_fee_due_ref(enr),
    )
    _set_status(enr, _S.FEE_SCHEDULED)
    _notify_fee_event(
        enr,
        "enrollment.fee_scheduled",
        valor=f"R$ {pr.amount}",
        due_date=plan["due_date"],
    )
    logger.info(
        "enrollment.fee_scheduled",
        external_id=str(enr.external_id),
        amount=str(pr.amount),
        due_date=plan["due_date"],
    )
    return {
        "external_id": str(enr.external_id),
        "status": enr.status,
        "fees": fee_facts(enr),
    }


def conclude(
    *,
    enrollment_external_id: str,
    coordinator,
    platform_login: str,
    platform_password: str,
    platform_url=None,
    platform_notes=None,
) -> Enrollment:
    """CONCLUSÃO (substitui o `release` antigo): com a 1ª parcela PAGA e a 2ª AGENDADA, o coordenador
    cadastra as credenciais da plataforma (fornecidas pela instituição — que só as libera com a 1ª paga)
    e o aluno vira student. Promoção ATÔMICA (role + COMPLETED + Student) — o miolo provado do release."""
    from users.roles.student import interface as student_iface

    enr = _enrollment_for_coordinator(
        enrollment_external_id,
        coordinator,
        _S.AWAITING_RELEASE,
        _S.FEE_PAID,
        _S.FEE_SCHEDULED,
    )
    facts = fee_facts(enr)
    missing = []
    if not facts["first_paid"]:
        missing.append("first_fee_paid")
    if not facts["second_scheduled"]:
        missing.append("second_fee_scheduled")
    if missing:
        raise Conflict(
            "A taxa ainda não está completa pra concluir a matrícula.",
            code="FEES_INCOMPLETE",
            extra={"missing": missing},
        )

    with transaction.atomic():
        if "student" not in roles.active_roles(enr.user):
            roles.promote(enr.user, "student")
        enr.status = _S.COMPLETED
        enr.save(update_fields=["status", "updated_at"])
        student_iface.create_from_enrollment(
            user=enr.user,
            hub=enr.hub,
            self_study=enr.self_study,
            platform_url=platform_url,
            platform_login=platform_login,
            platform_password=platform_password,
            platform_notes=platform_notes,
        )

    _notify_released(enr)
    logger.info("enrollment.concluded", external_id=str(enr.external_id))
    return enr


# ── reação aos hooks do finance (fee.paid / fee.problem — plan/14) ───────────


def apply_fee_paid(enr: Enrollment, *, external_reference: str, amount=None) -> bool:
    """Hook `fee.paid`: 1ª parcela paga → `fee_paid` (se ainda aguardando) + notify ao coordenador
    (é o gatilho do mundo real: a instituição só libera as credenciais com a 1ª paga). 2ª parcela
    paga no vencimento → só notify (o status já andou no agendamento)."""
    valor = f"R$ {amount}" if amount else "—"
    # match por PREFIXO: re-tentativas pós-falha carregam sufixo `_rN` na mesma família de ref.
    if external_reference.startswith(_fee_now_ref(enr)):
        if enr.status == _S.AWAITING_RELEASE:
            _set_status(enr, _S.FEE_PAID)
        _notify_fee_event(enr, "enrollment.fee_paid", valor=valor)
        logger.info("enrollment.fee_paid", external_id=str(enr.external_id))
        return True
    if external_reference.startswith(_fee_due_ref(enr)):
        _notify_fee_event(enr, "enrollment.fee_due_paid", valor=valor)
        logger.info("enrollment.fee_due_paid", external_id=str(enr.external_id))
        return True
    return False


def apply_fee_problem(
    enr: Enrollment, *, external_reference: str, detail=None, asaas_status=None
) -> bool:
    """Hook `fee.problem`: QUALQUER B.O. com a taxa (sem saldo, falha, erro) notifica o coordenador
    (palavra do Victor 2026-06-12). O status da matrícula NÃO regride — o coordenador re-posta a
    parcela (a fila re-arma via `_queue_fee`)."""
    if external_reference.startswith(_fee_now_ref(enr)):
        which = "1ª parcela (à vista)"
    elif external_reference.startswith(_fee_due_ref(enr)):
        which = "2ª parcela (agendada)"
    else:
        return False
    _notify_fee_event(
        enr,
        "enrollment.fee_problem",
        detail=f"{which} — {detail or 'erro desconhecido'}.",
        idem_suffix=f"_{asaas_status or 'err'}",
    )
    logger.warning(
        "enrollment.fee_problem",
        external_id=str(enr.external_id),
        ref=external_reference,
        detail=detail,
    )
    return True


def _notify_fee_event(
    enr: Enrollment, event: str, idem_suffix: str = "", **placeholders
) -> None:
    """Notify do ciclo da taxa → SEMPRE o COORDENADOR, nunca o aluno (política interna). Sem TTS."""
    from notify.interface.send import send
    from users.roles import notifications as msgs

    coord = enr.hub.coordinator
    if coord is None:
        logger.warning(
            "enrollment.fee_notify_no_coordinator", external_id=str(enr.external_id)
        )
        return
    cp = profiles.get(coord)
    sp = profiles.get(enr.user)
    try:
        send(
            text=msgs.text(
                event,
                name=msgs.first_name(cp.name if cp else None),
                student_name=(sp.name if sp else None) or "um aluno",
                **placeholders,
            ),
            caller=event,
            phone=cp.phone if cp else None,
            email=cp.email if cp else None,
            email_channel=bool(cp and cp.email),
            idempotency_key=f"{event}_{enr.external_id}{idem_suffix}",
        )
    except Exception as exc:  # noqa: BLE001 — notify nunca quebra o fluxo (§12)
        logger.warning("enrollment.fee_notify_failed", event=event, error=str(exc))


# ── visão do COORDENADOR: listagem/detalhe do polo + análises pendentes (plan/14) ───────────


def _hub_item_dict(enr: Enrollment) -> dict:
    p = profiles.get(enr.user)
    return {
        "external_id": str(enr.external_id),
        "name": p.name if p else None,
        "phone": p.phone if p else None,
        "status": enr.status,  # status REAL (visão do coordenador, sem máscara)
        "fees": fee_facts(enr),
        "created_at": enr.created_at.isoformat(),
    }


def list_for_staff(*, hub_external_id=None, status=None, limit=200) -> list[dict]:
    """Matrículas de TODOS os polos (ou de um, se `hub_external_id`) pro painel do staff. Read-only."""
    from users.profiles import interface as profiles

    qs = Enrollment.objects.select_related("user", "hub").order_by("-id")
    if hub_external_id:
        qs = qs.filter(hub__external_id=hub_external_id)
    if status:
        qs = qs.filter(status=status)
    rows = list(qs[:limit])
    pmap = profiles.get_map([r.user for r in rows])
    out = []
    for enr in rows:
        p = pmap.get(enr.user_id)
        out.append(
            {
                "external_id": str(enr.external_id),
                "status": enr.status,
                "self_study": enr.self_study,
                "hub_external_id": str(enr.hub.external_id),
                "name": p.name if p else None,
            }
        )
    return out


def list_for_hub(*, hub, status: str | None = None) -> list[dict]:
    """Matrículas do polo (visão do coordenador): status REAL + resumo das 2 parcelas da taxa.
    `?status=awaiting_release` = quem terminou o wizard e espera ação do coordenador."""
    qs = (
        Enrollment.objects.filter(hub=hub)
        .select_related("user")
        .order_by("-created_at")
    )
    if status:
        qs = qs.filter(status=status)
    return [_hub_item_dict(enr) for enr in qs]


def coordinated_user_ext(*, enrollment_external_id: str, coordinator) -> str:
    """Gate (coordenar o hub da matrícula) → external_id do USER, pra o coordenador AGIR NO LUGAR de
    um cliente sem prática digital (WP5). Reusa o gate do `_enrollment_for_coordinator`."""
    enr = _enrollment_for_coordinator(enrollment_external_id, coordinator)
    return str(enr.user.external_id)


def detail_for_hub(*, enrollment_external_id: str, coordinator) -> dict:
    """Detalhe COMPLETO de uma matrícula pro coordenador: a visão rica do /me (todas as seções)
    + status REAL (sem máscara) + fatos da taxa."""
    enr = _enrollment_for_coordinator(enrollment_external_id, coordinator)
    return {**me_dict(enr), "status": enr.status, "fees": fee_facts(enr)}


# campos de identidade DERIVADOS DO DOCUMENTO (OCR) que o coordenador pode corrigir. NÃO inclui
# `name`/`birth_date` (CPFHub é a fonte autoritativa) nem `pix` (validação própria) — Victor 2026-06-17.
_COORD_CORRECTABLE = (
    "mother_name",
    "father_name",
    "marital_status",
    "nationality",
    "birthplace",
)


def coordinator_correct_identity(
    *, enrollment_external_id: str, coordinator, **fields
) -> dict:
    """Coordenador corrige campos de identidade do Profile que o OCR extraiu errado (filiação, estado
    civil, naturalidade, nacionalidade) — sem isso uma extração torta fica gravada pra sempre e só um
    db-edit conserta (Victor 2026-06-17: user→coord, sem dev). SOBRESCREVE via `profiles.update_identity`.

    NÃO mexe em `name`/`birth_date` (CPFHub manda) nem em `pix`. Gate: coordenar o hub da matrícula."""
    enr = _enrollment_for_coordinator(enrollment_external_id, coordinator)
    clean = {
        k: v for k, v in fields.items() if k in _COORD_CORRECTABLE and v is not None
    }
    if not clean:
        raise DomainError(
            "Nenhum campo de identidade corrigível foi informado.", code="NO_FIELDS"
        )
    profiles.update_identity(enr.user, **clean)
    logger.info(
        "leadership.acted_for",
        action="correct_identity",
        enrollment=enrollment_external_id,
        fields=list(clean.keys()),
        by=str(coordinator.external_id),
    )
    return {**me_dict(enr), "status": enr.status}


def _sweep_stale_reviews(hub) -> None:
    """Resiliência (Victor 2026-06-17): se o worker da IA morreu/reiniciou (OOM), a análise fica
    PENDING calada — o TTL só vira `review` quando o PRÓPRIO aluno relê o /me. Se ele abandona, a
    análise some da vista de todos e só um db-edit destrava (= o que o Victor não quer em prod).

    Aqui, ao montar a fila do coordenador, todo PENDING que estourou o TTL VIRA `review` — assim
    aparece pra ele decidir (hierarquia user→coord, sem dev/flash de DB). Bulk update barato; uma
    vez em `review` não casa mais o filtro `=PENDING`."""
    from datetime import timedelta

    from django.utils import timezone

    from users.documents.models import RG
    from users.roles import _analysis, _selfie

    # selfie: o campo `selfie_taken_at` data o início → bulk update direto.
    cutoff = timezone.now() - timedelta(seconds=_analysis.ttl_seconds())
    Enrollment.objects.filter(
        hub=hub,
        selfie_status=_selfie.SelfieStatus.PENDING,
        selfie_taken_at__lt=cutoff,
    ).update(
        selfie_status=_selfie.SelfieStatus.REVIEW,
        selfie_description=_analysis.stale_reason(),
    )
    # RG: o model não tem `updated_at`; o início vive no JSON (`analysis_started_at`), igual ao flip
    # do /me do aluno (`_rg_started_at`). Loop curto (só os PENDING do polo) — sem referência → não mexe.
    user_ids = list(
        Enrollment.objects.filter(hub=hub).values_list("user_id", flat=True)
    )
    for rg in RG.objects.filter(
        document__user_id__in=user_ids, validation_status=_analysis.PENDING
    ):
        if _analysis.is_stale(rg.validation_status, _rg_started_at(rg)):
            rg.validation_status = _analysis.REVIEW
            rg.save(update_fields=["validation_status"])


def list_reviews_for_hub(*, hub) -> dict:
    """Análises da MATRÍCULA paradas esperando decisão do coordenador (RG e selfie em revisão).
    Cada item aponta pro POST de decisão que já existe (`/rg/decide`, `/selfie/decide`).
    Antes de listar, varre PENDING órfão (worker morto) → review (`_sweep_stale_reviews`)."""
    from users.roles import _analysis, _selfie

    _sweep_stale_reviews(hub)

    def _item(enr: Enrollment) -> dict:
        p = profiles.get(enr.user)
        return {
            "external_id": str(enr.external_id),
            "name": p.name if p else None,
            "since": enr.updated_at.isoformat(),
        }

    rg_qs = (
        Enrollment.objects.filter(
            hub=hub, user__document__rg__validation_status=_analysis.REVIEW
        )
        .select_related("user")
        .order_by("updated_at")
    )
    selfie_qs = (
        Enrollment.objects.filter(hub=hub, selfie_status=_selfie.SelfieStatus.REVIEW)
        .select_related("user")
        .order_by("updated_at")
    )
    return {"rg": [_item(e) for e in rg_qs], "selfie": [_item(e) for e in selfie_qs]}


def _notify_released(enr: Enrollment) -> None:
    from notify.interface.send import send
    from users.roles import notifications as msgs

    p = profiles.get(enr.user)
    try:
        send(
            text=msgs.text(
                "enrollment.released", name=msgs.first_name(p.name if p else None)
            ),
            caller="enrollment.released",
            phone=p.phone if p else None,
            email=p.email if p else None,
            email_channel=bool(p and p.email),
            tts=msgs.is_tts(
                "enrollment.released"
            ),  # virou aluno = momento especial (voz)
            gender=p.gender if p else None,
            idempotency_key=f"enr_released_{enr.external_id}",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("enrollment.notify_released_failed", error=str(exc))
