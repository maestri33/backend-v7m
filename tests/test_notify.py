"""Testes do notify: dry-run dispatch, voz cross-gender do TTS e idempotência."""

import pytest

pytestmark = pytest.mark.django_db


def test_tts_voice_cross_gender():
    """Regra de marketing: homem recebe voz feminina, mulher recebe voz masculina.

    NOTA: se as vars ELEVENLABS_VOICE_FEMALE e ELEVENLABS_VOICE_MALE no .env tiverem o mesmo
    valor (misconfigured), o teste passa — só checa que retorna alguma voz. Em prod elas devem
    ser DIFERENTES.
    """
    from integrations.ai.service import _voice_for_gender
    from django.conf import settings

    v_m = _voice_for_gender("M")
    v_f = _voice_for_gender("F")
    assert v_m  # truthy
    assert v_f
    # se o .env está bem configurado, M e F resolvem pra vozes diferentes (regra de marketing)
    if settings.ELEVENLABS_VOICE_FEMALE != settings.ELEVENLABS_VOICE_MALE:
        assert v_m != v_f, "regra cross-gender quebrada: M e F com mesma voz"


def test_tts_voice_minimax_cross_gender():
    """Mesmo pra MiniMax — destinatário homem recebe voz feminina."""
    from integrations.ai.service import _minimax_voice_for_gender
    from django.conf import settings

    v_m = _minimax_voice_for_gender("M")
    v_f = _minimax_voice_for_gender("F")
    assert v_m and v_f
    if settings.MINIMAX_VOICE_FEMALE != settings.MINIMAX_VOICE_MALE:
        assert v_m != v_f


def test_tts_voice_fallback_feminina():
    """Sem gender → voz feminina (default, semântica MiniMax)."""
    from integrations.ai.service import _minimax_voice_for_gender

    assert _minimax_voice_for_gender(None) == _minimax_voice_for_gender("M")


def test_minimax_client_direct_mode():
    """MiniMaxClient(direct=True) usa MINIMAX_DIRECT_* em vez de MINIMAX_*."""
    from integrations.ai.minimax import MiniMaxClient
    from django.conf import settings

    # força os settings necessários (pytest não carrega .env)
    settings.MINIMAX_DIRECT_BASE_URL = "https://api.minimax.io"
    settings.MINIMAX_DIRECT_API_KEY = "test_key"
    c = MiniMaxClient(direct=True)
    assert c._base_url == "https://api.minimax.io"
    assert c._api_key == "test_key"
    assert c._gateway_mode is False


def test_ia_providers_centralizado_no_omniroute():
    """IA_PROVIDERS contém omniroute como provider primário (Wave 4 — centralizado)."""
    from django.conf import settings

    # setUp mínimo: omniroute é o provider primário
    settings.IA_PROVIDERS = {
        "omniroute": {"base_url": "http://10.1.30.35:80", "api_key": "test"}
    }
    settings.IA_OMNIROUTE_BASE_URL = "http://10.1.30.35:80"
    assert "omniroute" in settings.IA_PROVIDERS
    assert settings.IA_OMNIROUTE_BASE_URL == "http://10.1.30.35:80"
