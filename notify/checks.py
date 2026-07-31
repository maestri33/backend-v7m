"""System check do app notify — avisa (não trava) quando falta config p/ um canal.

notify é consumidor: whatsapp/mail/ia já têm seus próprios checks E* que travam o boot. Aqui só
um Warning — sem uma base de URL a mídia do WhatsApp e o voice-note (TTS) não montam a URL pra
Evolution buscar, mas os outros canais funcionam, então não trava o manage.py (canal de apoio).

- `notify.W001` (Warning): sem MEDIA_LAN_BASE nem EXTERNAL_URL → o WhatsApp não tem como buscar
  a mídia/áudio por URL interna.
- `notify.E001`/`notify.E002` (Error): NOTIFY_MODE inválido, ou remote sem URL/api-key — modo
  remote mal configurado silenciaria TODAS as notificações, então trava o boot (fail-fast).
"""

from django.conf import settings
from django.core.checks import Error as DjangoError
from django.core.checks import Warning as DjangoWarning
from django.core.checks import register


def check_notify_env(app_configs, **kwargs):
    warnings = []
    if not (
        getattr(settings, "MEDIA_LAN_BASE", "") or getattr(settings, "EXTERNAL_URL", "")
    ):
        warnings.append(
            DjangoWarning(
                "MEDIA_LAN_BASE/EXTERNAL_URL ausentes — o WhatsApp do notify não monta a URL "
                "interna p/ a Evolution buscar a mídia/áudio (TTS).",
                hint="Defina MEDIA_LAN_BASE em backend/.env (ex.: http://10.1.20.30).",
                id="notify.W001",
            )
        )
    return warnings


# registrado via decorator: `apps.ready` importa este módulo, o que basta pro registro rodar.
@register
def check_notify_mode(app_configs, **kwargs):
    errors = []
    mode = getattr(settings, "NOTIFY_MODE", "local")
    if mode not in ("local", "remote"):
        errors.append(
            DjangoError(
                f"NOTIFY_MODE inválido: {mode!r} (esperado 'local' ou 'remote').",
                hint="Ajuste NOTIFY_MODE em backend/.env.",
                id="notify.E001",
            )
        )
    elif mode == "remote" and not (
        getattr(settings, "NOTIFY_URL", "") and getattr(settings, "NOTIFY_API_KEY", "")
    ):
        errors.append(
            DjangoError(
                "NOTIFY_MODE=remote exige NOTIFY_URL e NOTIFY_API_KEY não-vazios.",
                hint="Defina ambos em backend/.env (ou volte NOTIFY_MODE=local).",
                id="notify.E002",
            )
        )
    return errors
