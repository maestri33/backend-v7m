from pathlib import Path

from integrations.communication.mail import templates
from integrations.communication.mail.client import MailClient
from notify.seed import io as seed_io


V7M_EVENTS = {
    "candidate.awaiting_approval",
    "candidate.doc_type_reset",
    "candidate.document_approved",
    "candidate.document_in_review",
    "candidate.document_rejected",
    "candidate.address_proof_rejected",
    "candidate.rejected",
    "candidate.selfie_approved",
    "candidate.selfie_in_review",
    "candidate.selfie_rejected",
    "enrollment.awaiting_release",
    "enrollment.fee_due_paid",
    "enrollment.fee_paid",
    "enrollment.fee_problem",
    "enrollment.fee_scheduled",
    "enrollment.rg_in_review",
    "enrollment.selfie_in_review",
    "hub.coordinator_assigned",
    "lead.captured.promoter",
    "lead.paid.coordinator",
    "lead.paid.promoter",
    "lead.paid.promoter.scholarship",
    "promoter.scholarship_enrolled",
    "promoter.reactivated",
    "promoter.suspended",
    "student.document_in_review",
    "student.exam_scheduled",
    "student.veteran.coordinator",
    "training.approved",
    "training.approved.scholarship",
    "training.cleared",
    "training.must_train",
    "training.must_train.scholarship",
    "training.new_material",
}


def test_v7m_template_has_its_own_identity():
    html = templates.render("v7m", title="Cadastro aprovado", content="Tudo certo.")

    assert "Rede de promotores" in html
    assert "https://app.v7m.org" in html
    assert "https://app.supletivo.net.br" not in html


def test_supletivo_template_has_its_own_identity():
    html = templates.render("supletivo", title="Matrícula confirmada", content="Tudo certo.")

    assert "Supletivo <span" in html
    assert "https://app.supletivo.net.br" in html
    assert "https://app.v7m.org" not in html


def test_legacy_student_templates_use_supletivo_shell():
    assert templates.render("welcome", title="Bem-vindo", content="Olá") == templates.render(
        "supletivo", title="Bem-vindo", content="Olá"
    )


def test_mail_client_accepts_brand_sender_name(settings):
    settings.MAIL_FROM_NAME = "Nome padrão"
    settings.MAIL_FROM_EMAIL = "noreply@v7m.org"

    client = MailClient(from_name="V7M")

    assert client.from_header == "V7M <noreply@v7m.org>"


def test_seed_assigns_every_event_to_a_brand():
    seed_path = Path(__file__).resolve().parents[1] / "notify" / "seed" / "templates.md"
    specs = seed_io.parse(seed_path.read_text(encoding="utf-8"))

    assert len(specs) == 53
    assert all(spec.mail_template in {"supletivo", "v7m"} for spec in specs)
    assert {spec.event for spec in specs if spec.mail_template == "v7m"} == V7M_EVENTS
