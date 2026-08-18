"""G-uploads — as rotas de selfie/doc do aluno faziam `file.read()` cru: sem limite de tamanho
(2 GB → OOM), sem checar content-type, sem decodificar (bytes não-imagem persistidos). A validação
já existia em documents.upload_photo mas não rodava nessas rotas. Extraída em read_image_upload.
"""

import io

import pytest


class _FakeUpload:
    def __init__(self, content_type, size, data=b""):
        self.content_type = content_type
        self.size = size
        self._data = data

    def read(self):
        return self._data


def _png_bytes() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(buf, format="PNG")
    return buf.getvalue()


def test_uploads_rejeita_arquivo_grande_antes_de_ler():
    """Tamanho é checado ANTES de read() — não materializa 2 GB na memória."""
    from django.conf import settings

    from users.documents.service import read_image_upload
    from users.exceptions import ValidationError

    read_called = {"n": 0}

    class _Huge(_FakeUpload):
        def read(self):
            read_called["n"] += 1
            return b"x" * self.size

    big = _Huge("image/png", (settings.MAX_UPLOAD_MB + 1) * 1024 * 1024)
    with pytest.raises(ValidationError) as exc:
        read_image_upload(big)
    assert exc.value.code == "IMAGE_TOO_LARGE"
    assert read_called["n"] == 0, "leu o arquivo grande antes de rejeitar (OOM)"


def test_uploads_rejeita_content_type_invalido():
    from users.documents.service import read_image_upload
    from users.exceptions import ValidationError

    with pytest.raises(ValidationError) as exc:
        read_image_upload(_FakeUpload("application/x-msdownload", 100))
    assert exc.value.code == "IMAGE_TYPE_INVALID"


def test_uploads_rejeita_bytes_nao_imagem():
    """Content-type diz image/png mas os bytes não são imagem (renomeado) → decode falha."""
    from users.documents.service import read_image_upload
    from users.exceptions import ValidationError

    fake = _FakeUpload("image/png", 100, data=b"MZ\x90\x00 nao sou imagem")
    with pytest.raises(ValidationError) as exc:
        read_image_upload(fake)
    assert exc.value.code == "IMAGE_DECODE_FAILED"


def test_uploads_aceita_imagem_valida():
    from users.documents.service import read_image_upload

    data = _png_bytes()
    fake = _FakeUpload("image/png", len(data), data=data)
    out_bytes, ct = read_image_upload(fake)
    assert out_bytes == data
    assert ct == "image/png"


# ── endpoints que ANTES faziam file.read() cru (auditoria API B2) ──────────────
# O gap não era a função read_image_upload (já existia), era os ENDPOINTS que não a usavam:
# classify (candidate/enrollment) e a selfie do candidato. Agora recusam não-imagem na borda.


@pytest.mark.django_db
def test_endpoint_classify_recusa_nao_imagem_sem_chamar_ia(monkeypatch):
    """POST /candidate/documents/classify com bytes não-imagem → 422 IMAGE_DECODE_FAILED, e a IA
    paga NÃO é chamada (antes ia file.read() cru direto pro classify)."""
    import uuid

    from django.test import Client

    from integrations.ai import service as ai
    from users.auth.jwt import service as jwt
    from users.auth.models import User
    from users.roles import interface as roles

    called = {"n": 0}
    monkeypatch.setattr(
        ai, "classify_document", lambda *a, **k: called.__setitem__("n", 1)
    )

    u = User.objects.create_user(external_id=uuid.uuid4(), is_active=True)
    roles.assign(u, "candidate")
    token = jwt.issue(str(u.external_id), ["candidate"])["access_token"]

    # SimpleUploadedFile carrega um content_type real → exercita o decode; sem ele o Django manda
    # content_type vazio e a checagem de tipo dispara antes (também 422). Ambos provam o gate.
    from django.core.files.uploadedfile import SimpleUploadedFile

    resp = Client().post(
        "/api/v1/collaborators/candidate/documents/classify",
        data={
            "file": SimpleUploadedFile(
                "x.png", b"MZ\x90 nao sou imagem", content_type="image/png"
            )
        },
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )
    assert resp.status_code == 422
    assert resp.json().get("code") == "IMAGE_DECODE_FAILED"
    assert called["n"] == 0, "chamou a IA paga com lixo (não validou antes)"
