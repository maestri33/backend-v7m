"""Validação do COMPROVANTE DE ENDEREÇO por IA (F1, Victor 2026-07-08) — compartilhada por
`candidate` e `enrollment`.

Reusa o pipeline de visão+OCR+extração do `student` (chaveado por `doc_type="address_proof"`) — NÃO
tem IA própria. O que é NOVO aqui é o comparador `_address_matches`: o student só confere o titular
(`name_match`), nunca o ENDEREÇO extraído contra o informado. Aqui somamos as duas checagens.

Estados (espelham `_document_ai`, + um):
  • approved      — endereço bate + titular bate (name_match=sim)
  • rejected      — visão falha OU endereço não bate com o informado (peça pra corrigir/reenviar)
  • review        — IA em dúvida (name_match=duvida / IA fora do ar) → coordenador decide
  • needs_kinship — endereço bate mas o titular é OUTRO (name_match=nao): NÃO reprova; pede o grau
                    de parentesco (cônjuge/pai/mãe...) e libera depois ("não importa quem seja, mas
                    temos que saber pra não virar baderna").

# ponytail: `_address_matches` é heurística fuzzy (sem parsing oficial de CEP). Permissivo de
# propósito — só reprova em divergência CLARA de CEP ou cidade, nunca por typo de rua. Todo veredito
# é logado; se falso-negativo aparecer, sobe o limiar. Upgrade path: validar CEP no ViaCEP.
"""

from __future__ import annotations

import re
import unicodedata

import structlog

logger = structlog.get_logger()

APPROVED = "approved"
REJECTED = "rejected"
REVIEW = "review"
NEEDS_KINSHIP = "needs_kinship"

_DOC_TYPE = "address_proof"


def _norm(s: str | None) -> str:
    """minúsculo, sem acento, colapsa espaços — pra comparar rua/cidade sem falso-negativo bobo."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s.lower()).strip()


def _digits(s: str | None) -> str:
    return re.sub(r"\D", "", s or "")


def _address_matches(extracted: dict, address) -> tuple[bool, str]:
    """O endereço extraído do comprovante bate com o `Address` informado? (bool, motivo).

    Regra permissiva: reprova só se CEP OU cidade divergirem CLARAMENTE (ambos presentes e diferentes).
    A rua entra como reforço (overlap de tokens), não como veto isolado — comprovante abrevia/varia
    logradouro demais pra travar por isso. Campo ausente = não penaliza (dá o benefício da dúvida)."""
    if address is None or not any(
        getattr(address, field, None) for field in ("zipcode", "street", "city")
    ):
        return True, "Endereço será preenchido a partir do comprovante."

    ex_zip = _digits(extracted.get("zip"))
    in_zip = _digits(getattr(address, "zipcode", None))
    if ex_zip and in_zip and ex_zip != in_zip:
        return False, f"CEP do comprovante ({ex_zip}) difere do informado ({in_zip})."

    ex_city = _norm(extracted.get("city"))
    in_city = _norm(getattr(address, "city", None))
    if ex_city and in_city and ex_city != in_city:
        return (
            False,
            f"Cidade do comprovante ({ex_city}) difere da informada ({in_city}).",
        )

    # reforço leve: se CEP e cidade nada disseram (ambos ausentes de um lado), a rua precisa ao menos
    # tocar. Só reprova se houver rua nos dois lados e ZERO palavra em comum.
    ex_street = set(_norm(extracted.get("street")).split())
    in_street = set(_norm(getattr(address, "street", None)).split())
    if not (ex_zip and in_zip) and not (ex_city and in_city):
        if ex_street and in_street and ex_street.isdisjoint(in_street):
            return (
                False,
                "Nem CEP, nem cidade, nem rua bateram com o endereço informado.",
            )

    return True, "Endereço confere com o informado."


def run_validation(
    image_bytes: bytes,
    *,
    address,
    holder_name: str | None,
    mime_type: str = "image/jpeg",
    caller: str,
) -> tuple[str, dict]:
    """Valida 1 comprovante: visão → (endereço bate?) → (titular bate?). Devolve (status, payload).
    Payload guarda `vision`, `extracted`, `address_match` e `reason` (motivo final)."""
    from users.roles.student import _document_ai as doc_ai

    # (a) Visão: é um comprovante de endereço legível?
    vision_status, vision_reason = doc_ai.check_student_document_photo(
        image_bytes, doc_type=_DOC_TYPE, mime_type=mime_type, caller=caller
    )
    result: dict = {"vision": {"status": vision_status, "reason": vision_reason}}
    if vision_status == doc_ai.REJECTED:
        result["reason"] = vision_reason
        return REJECTED, result
    if vision_status != doc_ai.APPROVED:
        result["reason"] = vision_reason
        return REVIEW, result

    # (b) OCR + extração (endereço + titular).
    try:
        ocr_text = doc_ai.ocr_image(image_bytes, caller=caller)
        extracted = doc_ai.extract_student_document(
            ocr_text, doc_type=_DOC_TYPE, holder_name=holder_name, caller=caller
        )
    except Exception as exc:  # noqa: BLE001 — IA fora na extração → review
        logger.warning(
            "address_proof.extract_failed", caller=caller, error=str(exc)[:200]
        )
        result["reason"] = (
            "IA indisponível na extração — enviado para revisão manual do coordenador."
        )
        return REVIEW, result
    result["extracted"] = extracted

    # (c) o endereço extraído bate com o informado?
    addr_ok, addr_reason = _address_matches(extracted, address)
    result["address_match"] = {"ok": addr_ok, "reason": addr_reason}
    logger.info(
        "address_proof.address_match", caller=caller, ok=addr_ok, reason=addr_reason
    )
    if not addr_ok:
        result["reason"] = (
            f"O endereço do comprovante não confere com o informado. {addr_reason} "
            "Corrija o endereço ou envie um comprovante do endereço cadastrado."
        )
        return REJECTED, result

    # (d) titular: sobrenome bate → OK; outro → precisa explicar o parentesco (não reprova).
    match = str(extracted.get("name_match") or "").strip().lower()
    name_reason = (extracted.get("name_reason") or "").strip()
    if match in ("sim", "yes"):
        result["reason"] = name_reason or "Comprovante validado."
        return APPROVED, result
    if match in ("nao", "não", "no"):
        result["reason"] = (
            "O titular do comprovante é outra pessoa. Diga quem é e o grau de parentesco "
            f"(cônjuge, pai, mãe...). {name_reason}".strip()
        )
        return NEEDS_KINSHIP, result
    # duvida / ilegível → coordenador decide
    result["reason"] = f"Não deu pra confirmar o titular. {name_reason}".strip()
    return REVIEW, result


def _mime_of(path: str) -> str:
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    return "image/png" if ext == "png" else "image/jpeg"


def validate_and_store(user_external_id: str, *, caller: str) -> str:
    """Orquestração compartilhada (candidate/enrollment): lê a foto do comprovante já salva, roda a
    IA (endereço + titular), grava o veredito no `AddressProof` e devolve o status. Idempotente e
    best-effort: sem foto/perfil/endereço → `review` (nunca reprova no escuro). Chamada na task async."""
    from pathlib import Path

    from django.conf import settings
    from django.utils import timezone

    from users.address import interface as address_iface
    from users.documents import service as documents_iface
    from users.profiles import interface as profiles

    ap = documents_iface.get_address_proof(user_external_id)
    if ap is None or not ap.photo:
        return REVIEW
    fp = Path(settings.MEDIA_ROOT) / ap.photo
    if not fp.exists():
        return REVIEW
    p = profiles.find_by_external_id(user_external_id)
    address = p.address if p else None
    holder_name = p.name if p else None

    status, payload = run_validation(
        fp.read_bytes(),
        address=address,
        holder_name=holder_name,
        mime_type=_mime_of(ap.photo),
        caller=caller,
    )
    extracted = payload.get("extracted") if isinstance(payload, dict) else None
    if p is not None and isinstance(extracted, dict):
        try:
            address_iface.fill_empty(
                external_id=user_external_id,
                zipcode=extracted.get("zip") or extracted.get("zipcode"),
                street=extracted.get("street"),
                number=extracted.get("number"),
                complement=extracted.get("complement"),
                neighborhood=extracted.get("neighborhood"),
                city=extracted.get("city"),
                state=extracted.get("state"),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "address_proof.address_fill_failed", caller=caller, error=str(exc)[:200]
            )
    ap.validation_status = status
    ap.validation_result = payload
    ap.validated_at = timezone.now()
    ap.save(update_fields=["validation_status", "validation_result", "validated_at"])
    logger.info("address_proof.validated", caller=caller, status=status)

    # ponytail: o signal post_save do AddressProof cria o bloco (rejected) ou resolve (approved/pending).
    # Notify explícito só em rejeição (a aprovação o usuário descobre pelo /me normal).
    if status == REJECTED and p is not None:
        try:
            from notify.interface.events import send_event

            event = (
                "candidate.address_proof_rejected"
                if caller == "candidate.address_proof"
                else "enrollment.address_proof_rejected"
            )
            send_event(
                event,
                profile=p,
                subject="Seu comprovante de endereço precisa de ajuste",
                ctx={"detail": payload.get("reason", "")[:400]},
            )
        except Exception:  # noqa: BLE001
            logger.warning("address_proof.notify_failed", caller=caller, status=status)

    return status


def submit_kinship(user_external_id: str, relation: str) -> str:
    """Titular diferente (`needs_kinship`): a pessoa explica quem é / o parentesco. Grava e libera
    (→ approved). Só age se estava em `needs_kinship`. Devolve o novo status."""
    from django.utils import timezone

    from users.documents import service as documents_iface

    ap = documents_iface.get_address_proof(user_external_id)
    if ap is None:
        return REVIEW
    if ap.validation_status != NEEDS_KINSHIP:
        return ap.validation_status
    relation = (relation or "").strip()
    if not relation:
        return NEEDS_KINSHIP  # sem explicação → continua pendente
    # IA avalia se a explicação tem FUNDAMENTO e corrige o português. Sem fundamento (lixo/sem
    # sentido) → NÃO aprova; volta pra pessoa reescrever (human-in-the-loop). Fail-open dentro da IA.
    from integrations.ai import service as ai

    verdict = ai.evaluate_kinship(relation, caller="address_proof.kinship")
    if not verdict.get("has_merit"):
        return NEEDS_KINSHIP
    ap.kinship_relation = verdict.get("corrected") or relation
    ap.kinship_provided_at = timezone.now()
    ap.validation_status = APPROVED
    ap.save(
        update_fields=[
            "kinship_relation",
            "kinship_provided_at",
            "validation_status",
        ]
    )
    logger.info("address_proof.kinship_submitted", relation=ap.kinship_relation[:80])
    return APPROVED


def is_approved(user_external_id: str) -> bool:
    """Gate do wizard: o comprovante está aprovado? (usado no `_advance_address` dos dois funis)."""
    from users.documents import service as documents_iface

    ap = documents_iface.get_address_proof(user_external_id)
    return bool(ap and ap.validation_status == APPROVED)


def section_dict(user_external_id: str) -> dict:
    """Bloco do comprovante pro /me e GET (status + motivo + parentesco). `exists`=False = não enviado."""
    from users.documents import service as documents_iface

    ap = documents_iface.get_address_proof(user_external_id)
    if ap is None or not ap.photo:
        return {
            "exists": False,
            "photo": None,
            "status": None,
            "reason": None,
            "needs_kinship": False,
            "kinship_relation": None,
        }
    result = ap.validation_result or {}
    return {
        "exists": True,
        "photo": ap.photo,
        "status": ap.validation_status,
        "reason": result.get("reason") if isinstance(result, dict) else None,
        "needs_kinship": ap.validation_status == NEEDS_KINSHIP,
        "kinship_relation": ap.kinship_relation,
    }


def demo() -> None:
    """Self-check do comparador de endereço (função pura). `python users/roles/_address_proof.py`."""

    class A:  # stub do Address (só os campos que o comparador lê)
        def __init__(self, zipcode=None, street=None, city=None):
            self.zipcode = zipcode
            self.street = street
            self.city = city

    # match exato
    ok, _ = _address_matches(
        {"zip": "01310-100", "city": "São Paulo", "street": "Av Paulista"},
        A(zipcode="01310100", city="Sao Paulo", street="Avenida Paulista"),
    )
    assert ok, "CEP+cidade iguais (com acento/abreviação) deviam bater"

    # rua acentuada/abreviada, mesmo CEP → bate (CEP manda)
    ok, _ = _address_matches(
        {"zip": "01310100", "street": "Av. Paulista"},
        A(zipcode="01310-100", street="Avenida Paulista", city="São Paulo"),
    )
    assert ok, "mesmo CEP deve bater apesar da rua abreviada"

    # cidade diferente → reprova
    ok, why = _address_matches(
        {"city": "Campinas", "street": "Rua X"},
        A(city="São Paulo", street="Rua X"),
    )
    assert not ok and "idade" in why, "cidade divergente deveria reprovar"

    # CEP divergente → reprova (só dígitos)
    ok, _ = _address_matches({"zip": "99999-000"}, A(zipcode="01310100"))
    assert not ok, "CEP divergente deveria reprovar"

    # nada informado no comprovante → não reprova (benefício da dúvida)
    ok, _ = _address_matches({}, A(zipcode="01310100", city="São Paulo"))
    assert ok, "comprovante sem dados não deve reprovar por si só"

    # sem CEP/cidade dos dois lados e ruas disjuntas → reprova
    ok, _ = _address_matches({"street": "Rua das Flores"}, A(street="Avenida Brasil"))
    assert not ok, "sem CEP/cidade e ruas totalmente diferentes deveria reprovar"

    print("ok _address_proof.demo")


if __name__ == "__main__":
    demo()
