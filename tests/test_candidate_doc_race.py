"""Paridade das 2 guardas anti-race do candidato com o funil do aluno (enrollment):

(a) G11 — re-upload durante a task: a foto do slot troca enquanto a visão roda (~10-60s). O path
    é FIXO por slot (<ext_id>/<slot>.<ext>), então o discriminador é a VERSÃO do slot
    (`photo_versions`, uuid novo a cada upload). Sem isso, o veredito da foto velha gravava
    sobre a nova no mesmo path. O merge roda sob `_lock_doc_sub` (select_for_update) — dois
    workers em slots diferentes não se sobrescrevem mais (lost update no JSON).
(b) recheck pós-OCR: OCR + extração levam ~15s; se o TTL-sweep ou o coordenador decidirem nesse
    meio, o veredito velho NÃO pode sobrescrever a decisão já gravada.
"""

from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------- (a) G11 version guard
def _run_photo(monkeypatch, tmp_path, *, reupload: bool):
    from users.roles import _document_ai as doc_ai
    from users.roles.candidate import service as cs

    (tmp_path / "a.jpg").write_bytes(b"fake-doc")
    monkeypatch.setattr(cs.settings, "MEDIA_ROOT", str(tmp_path))

    saved = []

    class _Sub:
        pk = 1
        validation_status = doc_ai.PENDING
        validation_result = {"photo_versions": {"cnh_full": "v1"}}
        full_photo = "a.jpg"
        front_photo = None
        back_photo = None
        number = None

        def save(self, **kw):
            saved.append((self.validation_status, kw.get("update_fields")))

    sub = _Sub()

    class _Cand:
        id = 1
        doc_type = "cnh"
        external_id = "c1"
        user = type("U", (), {"external_id": "u1"})()
        hub = None

    mgr = MagicMock()
    mgr.select_related.return_value.filter.return_value.first.return_value = _Cand()
    monkeypatch.setattr(cs.Candidate, "objects", mgr)
    monkeypatch.setattr(cs.documents_iface, "get_doc_sub", lambda *a, **k: sub)

    def _lock(current):
        # simula o que aconteceu no DB enquanto a visão rodava: re-upload bumpa a versão
        if reupload:
            current.validation_result = {"photo_versions": {"cnh_full": "v2"}}
        return current

    monkeypatch.setattr(cs, "_lock_doc_sub", _lock)
    monkeypatch.setattr(doc_ai, "fix_orientation", lambda *a, **k: None)
    monkeypatch.setattr(
        doc_ai, "check_photo", lambda *a, **k: (doc_ai.REJECTED, "foto borrada")
    )
    # evita efeitos colaterais (notify) — só o guard está sob teste
    monkeypatch.setattr(cs, "_notify_doc_event", lambda **k: None)

    cs.run_document_validation(1, "cnh_full")
    return saved


def test_g11_reupload_no_mesmo_path_descarta_veredito_da_foto_velha(
    monkeypatch, tmp_path
):
    """A versão do slot trocou no meio tempo (re-upload no MESMO path) → veredito velho fora."""
    saved = _run_photo(monkeypatch, tmp_path, reupload=True)
    assert saved == [], "veredito da foto velha gravou sobre a nova (race G11)"


def test_g11_sem_reupload_grava_normal(monkeypatch, tmp_path):
    """Não-regressão: sem re-upload, o veredito é gravado normalmente."""
    from users.roles import _document_ai as doc_ai

    saved = _run_photo(monkeypatch, tmp_path, reupload=False)
    assert saved and saved[0][0] == doc_ai.REJECTED


def test_merge_da_frente_preserva_resultado_do_verso(monkeypatch, tmp_path):
    """Lost update (P0 da auditoria): o veredito do verso, gravado pelo outro worker enquanto a
    visão da frente rodava, tem que SOBREVIVER ao merge da frente — e a seção completa extrai."""
    from users.roles import _document_ai as doc_ai
    from users.roles.candidate import service as cs

    (tmp_path / "front.jpg").write_bytes(b"front")
    (tmp_path / "back.jpg").write_bytes(b"back")
    monkeypatch.setattr(cs.settings, "MEDIA_ROOT", str(tmp_path))

    class _Sub:
        pk = 1
        validation_status = doc_ai.PENDING
        # estado do DB no momento do LOCK: o worker do verso já gravou o slot dele
        validation_result = {
            "photo_versions": {"cnh_front": "f1", "cnh_back": "b1"},
            "photos": {"cnh_back": {"status": doc_ai.APPROVED, "reason": "ok"}},
        }
        front_photo = "front.jpg"
        back_photo = "back.jpg"
        full_photo = None
        number = "123"

        def save(self, **kw):
            return None

    sub = _Sub()

    class _Cand:
        id = 1
        doc_type = "cnh"
        external_id = "c1"
        user = type("U", (), {"external_id": "u1"})()
        hub = None

    mgr = MagicMock()
    mgr.select_related.return_value.filter.return_value.first.return_value = _Cand()
    monkeypatch.setattr(cs.Candidate, "objects", mgr)
    # a leitura INICIAL (sem lock) vê o estado ANTIGO — sem o veredito do verso ainda
    stale = type(
        "S",
        (),
        {
            "pk": 1,
            "validation_status": doc_ai.PENDING,
            "validation_result": {
                "photo_versions": {"cnh_front": "f1", "cnh_back": "b1"}
            },
            "front_photo": "front.jpg",
            "back_photo": "back.jpg",
            "full_photo": None,
            "number": "123",
        },
    )()
    monkeypatch.setattr(cs.documents_iface, "get_doc_sub", lambda *a, **k: stale)
    monkeypatch.setattr(cs, "_lock_doc_sub", lambda current: sub)
    monkeypatch.setattr(doc_ai, "fix_orientation", lambda *a, **k: None)
    monkeypatch.setattr(
        doc_ai, "check_photo", lambda *a, **k: (doc_ai.APPROVED, "ok")
    )
    merged = []
    monkeypatch.setattr(
        cs,
        "_doc_extract_and_finish",
        lambda cand, current, result, images: merged.append(result),
    )

    cs.run_document_validation(1, "cnh_front")

    assert merged, "seção completa não extraiu (veredito do verso foi perdido no merge)"
    assert set(merged[0]["photos"]) == {"cnh_front", "cnh_back"}


# ---------------------------------------------------------------- (b) recheck pós-OCR
class _Img:
    def read_bytes(self):
        return b"x"


def _run_extract(monkeypatch, *, rearmed: bool):
    from users.roles import _document_ai as doc_ai
    from users.roles.candidate import service as cs

    finishes = []

    class _Sub:
        validation_status = doc_ai.PENDING
        validation_result = {}

        def refresh_from_db(self, fields=None):
            if rearmed:
                # coordenador (ou TTL-sweep) já decidiu enquanto o OCR rodava
                self.validation_status = doc_ai.REVIEW

    class _Cand:
        doc_type = "cnh"
        external_id = "c1"
        user = type("U", (), {"external_id": "u1"})()

    monkeypatch.setattr(cs.profiles, "get", lambda u: None)
    monkeypatch.setattr(doc_ai, "ocr_images", lambda *a, **k: "texto")
    monkeypatch.setattr(
        doc_ai,
        "extract_document",
        lambda *a, **k: {"name_match": "sim", "name_reason": "ok"},
    )
    monkeypatch.setattr(cs, "_apply_doc_extracted", lambda *a, **k: None)
    monkeypatch.setattr(cs, "_doc_post_approval", lambda *a, **k: None)
    monkeypatch.setattr(cs, "_notify_doc_event", lambda **k: None)
    monkeypatch.setattr(
        cs, "_finish_doc", lambda cand, sub, status, *a, **k: finishes.append(status)
    )

    cs._doc_extract_and_finish(_Cand(), _Sub(), {}, [_Img()])
    return finishes


def test_recheck_pos_ocr_aborta_em_status_rearmado(monkeypatch):
    """Status re-armado durante o OCR (coordenador/TTL decidiu) → veredito velho NÃO grava."""
    finishes = _run_extract(monkeypatch, rearmed=True)
    assert finishes == [], "sobrescreveu decisão tomada durante o OCR (race pós-OCR)"


def test_recheck_pos_ocr_sem_corrida_finaliza(monkeypatch):
    """Não-regressão: sem corrida, a extração finaliza (aprova) normalmente."""
    from users.roles import _document_ai as doc_ai

    finishes = _run_extract(monkeypatch, rearmed=False)
    assert finishes == [doc_ai.APPROVED]
