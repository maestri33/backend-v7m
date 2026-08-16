"""Lógica do `documents` (CONVENTION §3) — DMZ. Cria/atualiza documentos e guarda fotos.

`create_empty` é chamado na transação do provisionamento (auth §9): nasce Document + RG/CNH/
Certificate/Military null. Foto vai pro filesystem (`media/documents/<external_id>/<slot>.<ext>`)
e o path relativo fica no campo do sub-doc. Militar só preenche pra `gender='M'` (Q4 do plano).
"""

from __future__ import annotations

from datetime import date

import structlog
from django.conf import settings
from django.core.files.storage import default_storage

from users.documents.models import CNH, RG, Certificate, Document, Military
from users.exceptions import NotFound, ValidationError
from users.profiles import interface as profiles

logger = structlog.get_logger()

# Campos editáveis por sub-doc (foto NÃO entra aqui — vai pelo upload). Datas parseadas à parte.
_TEXT_FIELDS = {
    "rg": ("number", "issuing_agency"),
    "cnh": ("number", "category", "national_register"),
    "certificate": ("kind", "number", "registry_office", "book", "page", "entry"),
    "military": ("number", "series", "category", "ra"),
}
_DATE_FIELDS = {
    "rg": ("issue_date",),
    "cnh": ("date_of_birth", "expires_on"),
    "certificate": ("issue_date",),
    "military": (),
}

# slot de foto → (atributo do sub-doc no Document, campo de path no sub-doc)
_PHOTO_SLOTS = {
    "rg_front": ("rg", "front_photo"),
    "rg_back": ("rg", "back_photo"),
    "rg_full": ("rg", "full_photo"),  # RG inteiro (frente+verso numa imagem) — plan/12
    "cnh_front": ("cnh", "front_photo"),
    "cnh_back": ("cnh", "back_photo"),
    "cnh_full": ("cnh", "full_photo"),  # CNH inteira (frente+verso) — plan/15 B2
    "certificate_photo": ("certificate", "photo"),
    "military_photo": ("military", "photo"),
}

# MIME aceito → extensão do arquivo salvo.
_ALLOWED_IMAGE = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
_PDF_MIME = (
    "application/pdf"  # aceito em qualquer slot: convertido pra JPEG antes de salvar
)
_PDF_MAX_PAGES = (
    2  # scan comum: pág 1 = frente, pág 2 = verso; >2 páginas → usa as 2 primeiras
)


def create_empty(user) -> Document:
    """Cria o Document + os 4 sub-docs vazios. DENTRO da transação do provisionamento (auth)."""
    document = Document.objects.create(user=user)
    RG.objects.create(document=document)
    CNH.objects.create(document=document)
    Certificate.objects.create(document=document)
    Military.objects.create(document=document)
    return document


def _get_document(external_id: str) -> Document:
    document = (
        Document.objects.filter(user__external_id=external_id)
        .select_related("rg", "cnh", "certificate", "military")
        .first()
    )
    if document is None:
        raise NotFound(
            "Documentos não encontrados para este usuário.", code="DOCUMENT_NOT_FOUND"
        )
    return document


def _parse_date(value):
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except (ValueError, TypeError) as exc:
        raise ValidationError(
            "Data inválida (use AAAA-MM-DD).", code="DATE_INVALID"
        ) from exc


def as_dict(document: Document) -> dict:
    """Serializa Document + sub-docs aninhados pro JSON da view."""

    def _photo(sub, *fields):
        return {f: getattr(sub, f) for f in fields}

    rg, cnh, cert, mil = (
        document.rg,
        document.cnh,
        document.certificate,
        document.military,
    )
    return {
        "external_id": str(document.user.external_id),
        "rg": {
            "number": rg.number,
            "issuing_agency": rg.issuing_agency,
            "issue_date": rg.issue_date.isoformat() if rg.issue_date else None,
            **_photo(rg, "front_photo", "back_photo", "full_photo"),
            "validation_status": rg.validation_status,
            "validation_reason": (rg.validation_result or {}).get("reason"),
        },
        "cnh": {
            "number": cnh.number,
            "category": cnh.category,
            "date_of_birth": cnh.date_of_birth.isoformat()
            if cnh.date_of_birth
            else None,
            "expires_on": cnh.expires_on.isoformat() if cnh.expires_on else None,
            "national_register": cnh.national_register,
            **_photo(cnh, "front_photo", "back_photo", "full_photo"),
            "validation_status": cnh.validation_status,
            "validation_reason": (cnh.validation_result or {}).get("reason"),
        },
        "certificate": {
            "kind": cert.kind,
            "number": cert.number,
            "registry_office": cert.registry_office,
            "book": cert.book,
            "page": cert.page,
            "entry": cert.entry,
            "issue_date": cert.issue_date.isoformat() if cert.issue_date else None,
            "photo": cert.photo,
        },
        "military": {
            "number": mil.number,
            "series": mil.series,
            "category": mil.category,
            "ra": mil.ra,
            "photo": mil.photo,
        },
    }


def get_by_external_id(external_id: str) -> dict:
    return as_dict(_get_document(external_id))


def get_rg(external_id: str) -> RG | None:
    """A instância RG do usuário (pro orquestrador da validação IA — enrollment, plan/12)."""
    return RG.objects.filter(document__user__external_id=external_id).first()


def get_cnh(external_id: str) -> CNH | None:
    """A instância CNH do usuário (plan/15 B2 — espelho do `get_rg`)."""
    return CNH.objects.filter(document__user__external_id=external_id).first()


def get_doc_sub(external_id: str, doc_type: str):
    """Devolve a instância do sub-doc pelo tipo (`rg` ou `cnh`). Helper do orquestrador de IA
    do funil (plan/15 B3) — espelha `get_rg` mas sem acoplar num tipo só."""
    if doc_type == "rg":
        return get_rg(external_id)
    if doc_type == "cnh":
        return get_cnh(external_id)
    raise ValueError(f"doc_type inválido: {doc_type!r}")


def update(external_id: str, payload: dict) -> dict:
    """Atualiza os campos enviados de cada sub-doc. Militar só pra `gender='M'` (Q4)."""
    document = _get_document(external_id)

    if "military" in payload and payload["military"]:
        profile = profiles.find_by_external_id(external_id)
        if profile is None or profile.gender != "M":
            raise ValidationError(
                "Documento militar só se aplica a usuários do gênero masculino.",
                code="MILITARY_NOT_APPLICABLE",
            )

    for sub_name, text_fields in _TEXT_FIELDS.items():
        sub_payload = payload.get(sub_name)
        if not sub_payload:
            continue
        sub = getattr(document, sub_name)
        for field in text_fields:
            if field in sub_payload:
                setattr(sub, field, sub_payload[field])
        for field in _DATE_FIELDS[sub_name]:
            if field in sub_payload:
                setattr(sub, field, _parse_date(sub_payload[field]))
        sub.save()

    logger.info(
        "documents.updated", external_id=external_id, parts=list(payload.keys())
    )
    return as_dict(document)


def _decode_image(data: bytes) -> None:
    """Confere que os bytes são uma IMAGEM de verdade (decode real, não só extensão) — plan/12."""
    from io import BytesIO

    from PIL import Image

    try:
        with Image.open(BytesIO(data)) as img:
            img.verify()
    except Exception as exc:  # noqa: BLE001 — Pillow lança tipos variados em arquivo corrompido
        raise ValidationError(
            "Arquivo não é uma imagem válida (corrompido ou renomeado).",
            code="IMAGE_DECODE_FAILED",
        ) from exc


def _pdf_to_jpeg(data: bytes) -> bytes:
    """Renderiza o PDF (até 2 páginas, empilhadas) numa imagem JPEG única — plan/12."""
    from io import BytesIO

    import pypdfium2 as pdfium
    from PIL import Image

    try:
        pdf = pdfium.PdfDocument(data)
        pages = [
            pdf[i].render(scale=2.0).to_pil()
            for i in range(min(len(pdf), _PDF_MAX_PAGES))
        ]
    except Exception as exc:  # noqa: BLE001 — PDF corrompido/protegido
        raise ValidationError(
            "Arquivo não é um PDF válido (corrompido ou protegido).",
            code="PDF_DECODE_FAILED",
        ) from exc
    if not pages:
        raise ValidationError("PDF sem páginas.", code="PDF_EMPTY")
    if len(pages) == 1:
        sheet = pages[0].convert("RGB")
    else:
        width = max(p.width for p in pages)
        sheet = Image.new("RGB", (width, sum(p.height for p in pages)), "white")
        y = 0
        for p in pages:
            sheet.paste(p.convert("RGB"), (0, y))
            y += p.height
    out = BytesIO()
    sheet.save(out, format="JPEG", quality=90)
    return out.getvalue()


def upload_photo(external_id: str, slot: str, upload) -> str:
    """Salva a imagem do slot em media/documents/<external_id>/<slot>.<ext> e grava o path no DB.

    Aceita JPEG/PNG/WEBP (decode real — arquivo renomeado não passa) e PDF (convertido pra JPEG
    internamente; no disco fica sempre imagem) — plan/12."""

    if slot not in _PHOTO_SLOTS:
        raise ValidationError(f"Slot de foto inválido: {slot}.", code="SLOT_INVALID")

    content_type = getattr(upload, "content_type", "")
    if content_type not in _ALLOWED_IMAGE and content_type != _PDF_MIME:
        raise ValidationError(
            "Arquivo deve ser JPEG, PNG, WEBP ou PDF.", code="IMAGE_TYPE_INVALID"
        )

    max_bytes = settings.MAX_UPLOAD_MB * 1024 * 1024
    if upload.size > max_bytes:
        raise ValidationError(
            f"Imagem maior que {settings.MAX_UPLOAD_MB} MB.", code="IMAGE_TOO_LARGE"
        )

    document = _get_document(external_id)
    sub_name, field = _PHOTO_SLOTS[slot]

    if sub_name == "military":
        profile = profiles.find_by_external_id(external_id)
        if profile is None or profile.gender != "M":
            raise ValidationError(
                "Documento militar só se aplica a usuários do gênero masculino.",
                code="MILITARY_NOT_APPLICABLE",
            )

    data = upload.read()
    if content_type == _PDF_MIME:
        data = _pdf_to_jpeg(data)
        ext = "jpg"
    else:
        _decode_image(data)
        ext = _ALLOWED_IMAGE[content_type]
    from core.media import save_media

    sub = getattr(document, sub_name)
    old = getattr(sub, field, None)
    path = save_media(prefix="documents", data=data, ext=ext)
    if old and old != path and default_storage.exists(old):
        default_storage.delete(old)
    setattr(sub, field, path)
    sub.save(update_fields=[field])
    logger.info("documents.photo_uploaded", external_id=external_id, slot=slot)
    return path


def delete_photo(external_id: str, slot: str) -> None:
    if slot not in _PHOTO_SLOTS:
        raise ValidationError(f"Slot de foto inválido: {slot}.", code="SLOT_INVALID")
    document = _get_document(external_id)
    sub_name, field = _PHOTO_SLOTS[slot]
    sub = getattr(document, sub_name)
    path = getattr(sub, field)
    if path and default_storage.exists(path):
        default_storage.delete(path)
    setattr(sub, field, None)
    sub.save(update_fields=[field])
    logger.info("documents.photo_deleted", external_id=external_id, slot=slot)
