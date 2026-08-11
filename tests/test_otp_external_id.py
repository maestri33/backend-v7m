"""OTP × notify (Fase 2): OtpCode guarda a STRING devolvida por send(), não mais a FK.

- Serviço: generate_and_send grava exatamente o retorno de send() em notification_external_id
  (funciona igual nos modos local e remote — send devolve str nos dois).
- Migrações 0033+0034 (reverso da 0012, EM DOIS PASSOS): 0033 adiciona a coluna e copia a FK ->
  string; 0034 remove a FK, só depois do restart (janela de deploy sem quebrar OTP — review
  adversarial pegou a versão de passo único quebrando o login no meio do deploy).
"""

import uuid

import pytest
from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from users.auth.otp import service as otp_service
from users.auth.otp.models import STATUS_SENT, OtpCode

pytestmark = pytest.mark.django_db


def _user_com_phone(phone="11999990010"):
    from users.auth.models import User
    from users.profiles.models import Profile

    user = User.objects.create_user()
    Profile.objects.create(user=user, phone=phone)
    return user


def test_model_sem_fk_para_notify():
    """A FK notification saiu do model; ficou a coluna solta UUIDField(null, blank)."""
    from django.db import models as dj_models

    fields = {f.name for f in OtpCode._meta.get_fields()}
    assert "notification" not in fields
    f = OtpCode._meta.get_field("notification_external_id")
    assert isinstance(f, dj_models.UUIDField)
    assert f.null is True
    assert f.blank is True


def test_otp_grava_string_devolvida_pelo_send(monkeypatch):
    """generate_and_send persiste EXATAMENTE o retorno de send() (str), sem lookup de model."""
    import notify.interface.send as notify_send

    sentinel = str(uuid.uuid4())
    monkeypatch.setattr(notify_send, "send", lambda **kwargs: sentinel)

    otp = otp_service.generate_and_send(_user_com_phone("11999990011"))

    assert otp.status == STATUS_SENT
    otp.refresh_from_db()
    # UUIDField lê de volta como objeto uuid.UUID (mesmo tendo sido atribuído como str no save).
    assert str(otp.notification_external_id) == sentinel


@pytest.mark.django_db(transaction=True)
def test_migracao_0033_copia_fk_para_string():
    """Aplica só a 0033 (AddField + RunPython copy) sobre uma row com FK preenchida.

    A FK ainda existe nesse ponto (0034 — RemoveField — não rodou): é exatamente o estado em
    que o código ANTIGO (ainda de pé até o restart) precisa continuar funcionando.
    """
    executor = MigrationExecutor(connection)
    try:
        # volta pro estado pré-0033 (FK notification ainda existe)
        old_state = executor.migrate([("users", "0032_validationblock")])
        old_apps = old_state.apps
        user = old_apps.get_model("users", "User").objects.create(
            external_id=uuid.uuid4(), password="!"
        )
        notif = old_apps.get_model("notify", "Notification").objects.create(
            caller="users.auth.otp", text="codigo 123456"
        )
        otp = old_apps.get_model("users", "OtpCode").objects.create(
            user=user, code_hash="x" * 64, notification_id=notif.id
        )

        # aplica SÓ a 0033 — a FK precisa sobreviver intacta (é o ponto de checagem do fix)
        executor = MigrationExecutor(connection)
        mid_state = executor.migrate(
            [("users", "0033_otpcode_notification_external_id")]
        )
        saved = mid_state.apps.get_model("users", "OtpCode").objects.get(id=otp.id)
        assert str(saved.notification_external_id) == str(notif.external_id)
        assert saved.notification_id == notif.id  # FK ainda de pé (0034 não rodou)

        # agora sim a 0034 remove a FK — nada além da coluna deve mudar
        executor = MigrationExecutor(connection)
        executor.migrate([("users", "0034_remove_otpcode_notification")])
    finally:
        # garante o schema final pros demais testes, mesmo se algo acima falhar
        call_command("migrate", verbosity=0)


@pytest.mark.django_db(transaction=True)
def test_migracao_reversa_restaura_fk_com_dados_reais():
    """Reverso ponta a ponta: do estado atual (head) até 0032, com uma row REAL preenchida.

    Achado do review adversarial: o único teste que exercitava `restore_otp_notification` fazia
    isso incidentalmente (setup migrando pra trás numa tabela ainda vazia). Este teste cria a row
    no estado FINAL (só notification_external_id, sem FK) e confere que desmigrar restaura a FK.
    """
    # a suíte já está no head (pytest-django migra o DB de teste antes de qualquer teste rodar)
    # — cria a row com os models REAIS, sem precisar de app registry histórico pra isso.
    from notify.models import Notification

    user = _user_com_phone("11999990013")
    notif = Notification.objects.create(caller="users.auth.otp", text="codigo 654321")
    otp = OtpCode.objects.create(
        user=user, code_hash="y" * 64, notification_external_id=str(notif.external_id)
    )

    executor = MigrationExecutor(connection)
    try:
        # desmigra até 0032: passa por 0034 (reverso = re-adiciona a FK) e 0033 (reverso =
        # restore_otp_notification, que precisa achar a Notification pela string e religar a FK)
        old_state = executor.migrate([("users", "0032_validationblock")])
        restored = old_state.apps.get_model("users", "OtpCode").objects.get(id=otp.id)
        assert restored.notification_id == notif.id
    finally:
        call_command("migrate", verbosity=0)
