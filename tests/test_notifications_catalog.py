from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.contrib.admin.sites import AdminSite
from django.core.exceptions import ValidationError
from django.utils import timezone


pytestmark = pytest.mark.django_db


def test_seed_creates_fixed_catalog_and_keeps_training():
    from notifications.models import NotificationTemplate
    from notifications.seed import seed_notification_templates

    seed_notification_templates()

    assert NotificationTemplate.objects.filter(event="auth.otp").exists()
    assert NotificationTemplate.objects.filter(event="training.must_train").exists()
    assert NotificationTemplate.objects.filter(event="training.new_material").exists()


def test_every_seeded_template_is_valid():
    from notifications.models import NotificationTemplate
    from notifications.seed import seed_notification_templates

    seed_notification_templates()

    for template in NotificationTemplate.objects.all():
        template.full_clean()


def test_seed_updates_default_but_preserves_admin_customization():
    from notifications.defaults import NOTIFICATION_DEFAULTS
    from notifications.models import NotificationTemplate
    from notifications.seed import seed_notification_templates

    seed_notification_templates()
    default_body = NOTIFICATION_DEFAULTS["lead.paid"]["body"]

    NotificationTemplate.objects.filter(event="lead.paid").update(
        body="desatualizado", customized_at=None
    )
    seed_notification_templates()
    template = NotificationTemplate.objects.get(event="lead.paid")
    assert template.body == default_body

    template.body = "texto decidido no admin"
    template.customized_at = timezone.now()
    template.save(update_fields=["body", "customized_at", "updated_at"])
    seed_notification_templates()
    template.refresh_from_db()
    assert template.body == "texto decidido no admin"


def test_event_is_immutable_after_creation():
    from notifications.models import NotificationTemplate
    from notifications.seed import seed_notification_templates

    seed_notification_templates()
    template = NotificationTemplate.objects.get(event="lead.paid")
    template.event = "lead.renamed"

    with pytest.raises(ValidationError):
        template.full_clean()


def test_admin_content_rejects_malformed_or_unsafe_placeholder():
    from notifications.models import NotificationTemplate
    from notifications.seed import seed_notification_templates

    seed_notification_templates()
    template = NotificationTemplate.objects.get(event="lead.paid")

    template.body = "Olá, {nome"
    with pytest.raises(ValidationError):
        template.full_clean()


def test_admin_can_use_all_name_aliases_provided_by_backend():
    from notifications.models import NotificationTemplate
    from notifications.seed import seed_notification_templates

    seed_notification_templates()
    template = NotificationTemplate.objects.get(event="lead.paid")
    template.body = "Olá {nome-completo}; também {nome_completo}, {nome} e {name}."

    template.full_clean()


def test_admin_media_requires_explicit_safe_host(settings):
    from notifications.models import NotificationTemplate
    from notifications.seed import seed_notification_templates

    seed_notification_templates()
    template = NotificationTemplate.objects.get(event="lead.paid")
    settings.NOTIFICATION_MEDIA_ALLOWED_HOSTS = []
    template.media_url = "http://127.0.0.1/internal"
    with pytest.raises(ValidationError):
        template.full_clean()

    settings.NOTIFICATION_MEDIA_ALLOWED_HOSTS = ["media.example.test"]
    template.media_url = "https://media.example.test/files/welcome.mp3"
    template.full_clean()

    template.body = "Tipo interno: {nome.__class__}"
    with pytest.raises(ValidationError):
        template.full_clean()

    for unsafe in ("Formato gigante: {nome:>10000000}", "Conversão: {nome!r}"):
        template.body = unsafe
        with pytest.raises(ValidationError):
            template.full_clean()


def test_send_event_revalidates_legacy_media_before_dispatch(monkeypatch, settings):
    from notifications.delivery import send_event
    from notifications.models import NotificationTemplate
    from notifications.seed import seed_notification_templates

    seed_notification_templates()
    settings.NOTIFICATION_MEDIA_ALLOWED_HOSTS = []
    NotificationTemplate.objects.filter(event="lead.paid").update(
        media_url="http://127.0.0.1/internal"
    )
    monkeypatch.setattr(
        "integrations.notify.delivery.send",
        lambda **payload: pytest.fail("URL legada perigosa não pode chegar ao notify-server"),
    )

    with pytest.raises(ValidationError):
        send_event("lead.paid", phone="5511999999999", ctx={"nome": "Maria"})


def test_storytelling_uses_ai_and_preserves_fixed_fallback(monkeypatch, settings):
    from notifications.delivery import send_event
    from notifications.models import NotificationTemplate
    from notifications.seed import seed_notification_templates

    seed_notification_templates()
    settings.NOTIFICATION_STORYTELLING_ENABLED = True
    template = NotificationTemplate.objects.get(event="student.diploma_issued")
    assert template.storytelling is True
    assert template.story_prompt
    generated = []
    sent = []
    monkeypatch.setattr(
        "integrations.ai.service.generate_text",
        lambda prompt, **kwargs: generated.append((prompt, kwargs))
        or "Maria, você concluiu seus estudos e essa vitória será sua para sempre.",
    )
    monkeypatch.setattr(
        "integrations.notify.delivery.send",
        lambda **payload: sent.append(payload) or "notification-id",
    )

    result = send_event(
        "student.diploma_issued",
        phone="5511999999999",
        ctx={"nome": "Maria"},
        run_sync=True,
    )

    assert result == "notification-id"
    assert generated
    assert sent[0]["text"].startswith("Maria, você concluiu")


def test_storytelling_failure_falls_back_and_non_story_never_calls_ai(monkeypatch, settings):
    from notifications.delivery import send_event
    from notifications.models import NotificationTemplate
    from notifications.seed import seed_notification_templates

    seed_notification_templates()
    settings.NOTIFICATION_STORYTELLING_ENABLED = True
    fallback = NotificationTemplate.objects.get(event="student.diploma_issued").body
    sent = []

    def fail_ai(*args, **kwargs):
        raise RuntimeError("IA indisponível")

    monkeypatch.setattr("integrations.ai.service.generate_text", fail_ai)
    monkeypatch.setattr(
        "integrations.notify.delivery.send",
        lambda **payload: sent.append(payload) or "notification-id",
    )

    send_event(
        "student.diploma_issued",
        phone="5511999999999",
        ctx={"nome": "Maria"},
        run_sync=True,
    )
    assert sent[-1]["text"] == fallback.format(nome="Maria", name="Maria")

    send_event(
        "lead.paid",
        phone="5511999999999",
        ctx={"nome": "Maria"},
        run_sync=True,
    )
    assert len(sent) == 2


def test_storytelling_is_privacy_opt_in_and_defaults_to_fixed_content(monkeypatch, settings):
    from notifications.delivery import send_event
    from notifications.models import NotificationTemplate
    from notifications.seed import seed_notification_templates

    seed_notification_templates()
    settings.NOTIFICATION_STORYTELLING_ENABLED = False
    fallback = NotificationTemplate.objects.get(event="student.diploma_issued").body
    sent = []
    monkeypatch.setattr(
        "integrations.ai.service.generate_text",
        lambda *args, **kwargs: pytest.fail("PII não pode sair sem opt-in explícito"),
    )
    monkeypatch.setattr(
        "integrations.notify.delivery.send",
        lambda **payload: sent.append(payload) or "notification-id",
    )

    send_event(
        "student.diploma_issued",
        phone="5511999999999",
        ctx={"nome": "Maria"},
    )

    assert sent[0]["text"] == fallback.format(nome="Maria", name="Maria")


def test_tts_is_only_requested_when_whatsapp_is_selected(monkeypatch):
    from notifications.delivery import send_event
    from notifications.models import NotificationTemplate
    from notifications.seed import seed_notification_templates

    seed_notification_templates()
    NotificationTemplate.objects.filter(event="lead.paid").update(channels="email")
    sent = []
    monkeypatch.setattr(
        "integrations.notify.delivery.send",
        lambda **payload: sent.append(payload) or "notification-id",
    )

    send_event("lead.paid", email="maria@example.test", ctx={"nome": "Maria"})

    assert sent[0]["tts"] is False


def test_document_rejection_accepts_reason_or_no_reason(monkeypatch):
    from notifications.delivery import send_event
    from notifications.seed import seed_notification_templates

    seed_notification_templates()
    sent = []
    monkeypatch.setattr(
        "integrations.notify.delivery.send",
        lambda **payload: sent.append(payload) or "notification-id",
    )

    send_event(
        "student.document_rejected",
        phone="5511999999999",
        ctx={"nome": "Maria", "doc_type": "RG", "reason": "foto cortada"},
    )
    send_event(
        "student.document_rejected",
        phone="5511999999999",
        ctx={"nome": "Maria", "doc_type": "RG"},
    )

    assert "Motivo: foto cortada." in sent[0]["text"]
    assert "{reason_text}" not in sent[1]["text"]


def test_admin_allows_edit_but_forbids_create_and_delete():
    from notifications.admin import NotificationTemplateAdmin
    from notifications.models import NotificationTemplate

    model_admin = NotificationTemplateAdmin(NotificationTemplate, AdminSite())
    request = SimpleNamespace(user=SimpleNamespace(is_superuser=True))

    assert model_admin.has_add_permission(request) is False
    assert model_admin.has_delete_permission(request) is False
    assert "event" in model_admin.get_readonly_fields(request)


def test_admin_edit_marks_author_and_customization():
    from notifications.admin import NotificationTemplateAdmin
    from notifications.models import NotificationTemplate
    from notifications.seed import seed_notification_templates
    from users.auth.models import User

    seed_notification_templates()
    author = User.objects.create_user(is_staff=True)
    template = NotificationTemplate.objects.get(event="lead.paid")
    template.body = "Conteúdo personalizado no admin."
    model_admin = NotificationTemplateAdmin(NotificationTemplate, AdminSite())

    model_admin.save_model(
        SimpleNamespace(user=author),
        template,
        SimpleNamespace(changed_data=["body"]),
        change=True,
    )

    template.refresh_from_db()
    assert template.body == "Conteúdo personalizado no admin."
    assert template.customized_at is not None
    assert template.customized_by == author


def test_restore_default_clears_customization():
    from notifications.defaults import NOTIFICATION_DEFAULTS
    from notifications.models import NotificationTemplate
    from notifications.seed import seed_notification_templates

    seed_notification_templates()
    template = NotificationTemplate.objects.get(event="lead.paid")
    template.body = "Personalizado"
    template.customized_at = timezone.now()
    template.save()

    result = seed_notification_templates(force_events={"lead.paid"})

    template.refresh_from_db()
    assert result["updated"] == 1
    assert template.body == NOTIFICATION_DEFAULTS["lead.paid"]["body"]
    assert template.customized_at is None


def test_send_event_renders_database_content_and_sends_ready_payload(monkeypatch):
    from notifications.delivery import send_event
    from notifications.models import NotificationTemplate
    from notifications.seed import seed_notification_templates

    seed_notification_templates()
    NotificationTemplate.objects.filter(event="lead.paid").update(
        body="Pagamento confirmado para {nome}: {valor}.",
        channels="whatsapp,email",
        customized_at=timezone.now(),
    )
    sent = []
    monkeypatch.setattr(
        "integrations.notify.delivery.send",
        lambda **payload: sent.append(payload) or "notification-id",
    )

    result = send_event(
        "lead.paid",
        phone="5511999999999",
        email="maria@example.test",
        ctx={"nome": "Maria", "valor": "R$ 10,00"},
        run_sync=True,
    )

    assert result == "notification-id"
    assert sent == [
        {
            "text": "Pagamento confirmado para Maria: R$ 10,00.",
            "caller": "lead.paid",
            "phone": "5511999999999",
            "email": "maria@example.test",
            "title": None,
            "subject": None,
            "whatsapp": True,
            "email_channel": True,
            "tts": True,
            "media_url": None,
            "media_type": None,
            "gender": None,
            "mail_template": "default",
            "idempotency_key": None,
            "run_sync": True,
        }
    ]


def test_send_event_rejects_missing_context_before_notify_server(monkeypatch):
    from notifications.delivery import send_event
    from notifications.seed import seed_notification_templates

    seed_notification_templates()
    monkeypatch.setattr(
        "integrations.notify.delivery.send",
        lambda **payload: pytest.fail("não deve chamar o notify-server"),
    )

    with pytest.raises(ValidationError):
        send_event("lead.checkout.card", phone="5511999999999", ctx={})


def test_send_event_without_recipient_for_selected_channel_is_noop(monkeypatch):
    from notifications.delivery import send_event
    from notifications.models import NotificationTemplate
    from notifications.seed import seed_notification_templates

    seed_notification_templates()
    NotificationTemplate.objects.filter(event="lead.paid").update(channels="email")
    monkeypatch.setattr(
        "integrations.notify.delivery.send",
        lambda **payload: pytest.fail("não deve criar despacho sem destino do canal"),
    )

    assert send_event("lead.paid", phone="5511999999999") is None


def test_adhoc_rejects_empty_message_and_missing_recipient():
    from notifications import send_adhoc
    from users.exceptions import ValidationError as DomainValidationError

    with pytest.raises(DomainValidationError):
        send_adhoc(message=" ", phone="5511999999999")
    with pytest.raises(DomainValidationError):
        send_adhoc(message="mensagem sem destino")


def test_adhoc_rejects_unknown_channel():
    from notifications import send_adhoc
    from users.exceptions import ValidationError as DomainValidationError

    with pytest.raises(DomainValidationError):
        send_adhoc(
            message="teste",
            phone="5511999999999",
            channels=["telegram"],
        )
