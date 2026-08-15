"""Contrato do aluno: documento pessoal é RG/CIN; CNH nunca é aceita."""


def test_student_id_card_prompt_rejeita_cnh(monkeypatch):
    from integrations.ai import service as ai
    from users.roles.student import _document_ai
    from users.roles.student.models import StudentDocument

    captured = {}

    def describe_image(*args, **kwargs):
        captured["prompt"] = kwargs["prompt"]
        return "REPROVADO. Isso é uma CNH; envie o RG."

    monkeypatch.setattr(ai, "describe_image", describe_image)

    status, reason = _document_ai.check_student_document_photo(
        b"fake",
        doc_type=StudentDocument.Type.ID_CARD,
        caller="test.student.rg_only",
    )

    assert "NÃO aceite CNH" in captured["prompt"]
    assert status == _document_ai.REJECTED
    assert "CNH" in reason
