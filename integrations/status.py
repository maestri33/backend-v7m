"""Status unificado das integrações externas pro painel do STAFF (WP6, Victor 2026-06-16).

Cada integração tem um fluxo próprio. Aqui a visão READ-ONLY (env presente — só BOOL, nunca o valor do
secret — + o último resultado do ledger `ValidationCheck`) + ações (asaas tem setup/teste AO VIVO; os
demais reportam o último do ledger, já que o health real deles roda por command assíncrono/pesado).
"""

from __future__ import annotations

from django.conf import settings

from core.validation import latest_checks

_REGISTRY: dict[str, dict] = {
    "asaas": {
        "env": [
            "ASAAS_API_KEY",
            "ASAAS_WEBHOOK_SECRET",
            "ASAAS_BASE_URL",
            "EXTERNAL_URL",
        ],
        "scope": "asaas",
        "flow": "onboarding → auto-cadastro do webhook → self-test (saldo) + transfer-validation",
    },
    "infinitepay": {
        "env": ["INFINITEPAY_HANDLE", "INFINITEPAY_BASE_URL", "EXTERNAL_URL"],
        "scope": "infinitepay",
        "flow": "checkout (autentica pelo handle) → webhook (order_nsu opaco) → payment_check",
    },
    "whatsapp": {
        "env": ["WHATSAPP_API_BASE_URL", "WHATSAPP_API_KEY"],
        "scope": "whatsapp",
        "flow": "Evolution GO (instância definida pelo token): status + texto/mídia/áudio PTT",
    },
    "mail": {
        "env": [
            "MAIL_SMTP_HOST",
            "MAIL_SMTP_USER",
            "MAIL_SMTP_PASSWORD",
            "MAIL_FROM_EMAIL",
        ],
        "scope": "mail",
        "flow": "SMTP STARTTLS:587 (login) → validação MX/RCPT → send (templates)",
    },
    "ai": {
        "env": [
            "MINIMAX_API_KEY",
            "GEMINI_API_KEY",
            "ELEVENLABS_API_KEY",
            "GOOGLE_VISION_API_KEY",
        ],
        "scope": "ai",
        "flow": "LLM (M3→deepseek→gemini) + visão + TTS (MiniMax→ElevenLabs) + OCR (Google Vision)",
    },
    "biometric": {
        "env": ["BIOMETRIC_MODEL_NAME"],
        "scope": "biometric",
        "flow": "InsightFace buffalo_l (CPU): face-match documento×selfie",
    },
    "cep": {
        "env": [],
        "scope": "cep",
        "flow": "ViaCEP (API pública, sem key)",
    },
    "cpf": {
        "env": ["CPFHUB_API_KEY", "CPFHUB_BASE_URL"],
        "scope": "cpf",
        "flow": "CPFHub.io (header x-api-key): CPF → identidade",
    },
}


def _config(integ: dict) -> dict:
    """Só BOOL de presença da env (NUNCA o valor do secret)."""
    return {name: bool(getattr(settings, name, "")) for name in integ["env"]}


def _summary(name: str, integ: dict) -> dict:
    cfg = _config(integ)
    return {
        "name": name,
        "configured": all(cfg.values()) if cfg else True,  # cep não tem env
        "config": cfg,
        "flow": integ["flow"],
        "checks": latest_checks(integ["scope"]),
    }


def list_integrations() -> list[dict]:
    """Visão READ-ONLY de TODAS as integrações (config + último resultado do ledger). Sem rede."""
    return [_summary(name, integ) for name, integ in _REGISTRY.items()]


def integration_detail(name: str) -> dict | None:
    """Detalhe de uma integração. Pro asaas, faz o run_checks AO VIVO (saldo + webhook — rede)."""
    integ = _REGISTRY.get(name)
    if integ is None:
        return None
    data = _summary(name, integ)
    if name == "asaas":
        from integrations.bank.asaas import onboarding

        try:  # run_checks faz rede (saldo/webhook); timeout/erro não-AsaasError → erro estruturado
            data["live"] = onboarding.run_checks(record=False)
        except Exception as e:  # noqa: BLE001
            data["live"] = {"error": str(e)}
    return data


def run_setup(name: str) -> dict | None:
    """Ação de onboarding (asaas: auto-cadastra o webhook). Idempotente. Só asaas tem ação real."""
    if name not in _REGISTRY:
        return None
    if name == "asaas":
        from integrations.bank.asaas import onboarding

        return onboarding.setup()
    return {
        "detail": f"'{name}' não tem ação de setup (config via .env; use /test pra checar)."
    }


def run_test(name: str) -> dict | None:
    """Teste de saúde ao vivo (carimba o ledger). Asaas: run_checks real. Demais: último do ledger
    (o teste ao vivo desses serviços roda pelos commands de health — assíncrono/pesado)."""
    integ = _REGISTRY.get(name)
    if integ is None:
        return None
    if name == "asaas":
        from integrations.bank.asaas import onboarding

        return onboarding.run_checks(record=True)
    return {
        "name": name,
        "checks": latest_checks(integ["scope"]),
        "detail": "teste ao vivo deste serviço roda pelo command de health; aqui o último do ledger.",
    }
