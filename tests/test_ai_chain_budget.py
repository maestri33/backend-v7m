"""Orçamento da cadeia de IA (auditoria R5): cada provider morto custava até 3×timeout antes do
fallback — cadeia com 2 ruins estourava o Q_TIMEOUT do worker (task morta, re-entregue, crédito
em triplicata). Agora: com fallback na cadeia, 1 tentativa intra-provider (o retry É o próximo
provider) e a cadeia inteira respeita IA_CHAIN_DEADLINE_S.
"""

import pytest

from integrations.ai import service
from integrations.ai.client import LLMError

pytestmark = pytest.mark.django_db


class _DeadClient:
    provider = "p"


def _failing_attempt():
    async def attempt(client, model):
        raise LLMError("provider fora", retryable=True)

    return attempt


def test_intra_attempts_1_quando_ha_fallback(monkeypatch):
    """Cadeia com 2 providers → cada client é criado com attempts=1 (o fallback é o retry)."""
    seen = []

    def _get_client(provider, *, attempts=None):
        seen.append(attempts)
        return _DeadClient()

    monkeypatch.setattr(service.providers, "get_client", _get_client)
    with pytest.raises(LLMError):
        service._run("json", "test", _failing_attempt(), [("p1", "m1"), ("p2", "m2")])
    assert seen == [1, 1]


def test_intra_attempts_3_sem_fallback(monkeypatch):
    """Cadeia de 1 provider → mantém as 3 tentativas intra-provider (não há pra quem cair)."""
    seen = []

    def _get_client(provider, *, attempts=None):
        seen.append(attempts)
        return _DeadClient()

    monkeypatch.setattr(service.providers, "get_client", _get_client)
    with pytest.raises(LLMError):
        service._run("json", "test", _failing_attempt(), [("p1", "m1")])
    assert seen == [3]


def test_deadline_estourado_nao_tenta_o_proximo_provider(monkeypatch):
    """Budget esgotado após a 1ª falha → levanta o último erro SEM gastar no 2º provider."""
    calls = []

    def _get_client(provider, *, attempts=None):
        calls.append(provider)
        return _DeadClient()

    monkeypatch.setattr(service.providers, "get_client", _get_client)
    # relógio fake SEM fim (asyncio também consome monotonic): 2 primeiras leituras = t0
    # (deadline e started); daí em diante o tempo já estourou o budget.
    state = {"n": 0}

    def _clock():
        state["n"] += 1
        return 0.0 if state["n"] <= 2 else 10_000.0

    monkeypatch.setattr(service.time, "monotonic", _clock)
    with pytest.raises(LLMError):
        service._run(
            "json", "test", _failing_attempt(), [("p1", "m1"), ("p2", "m2")]
        )
    assert calls == ["p1"], "cadeia continuou depois do deadline estourado"
