"""Throttling das rotas públicas (auditoria API B1).

O projeto não tinha throttling NENHUM (grep -i throttl vazio), e `POST /clients/auth/check` é
anônimo e a cada request CRIA User+Profile+Lead e dispara OTP+WhatsApp — um script iterando
números BR fazia spam em massa do número oficial (risco de ban no provedor) e squatting de
telefones. O rate-limit de OTP existente é POR USUÁRIO, então não protege o caminho onde cada
request cria um usuário novo.

Duas peças:
- `ClientIpAnonThrottle`: throttle do django-ninja por IP nas rotas auth=None. O `get_ident` do
  ninja lê o XFF ESQUERDO (forjável); aqui usamos `core.net.client_ip` (ancorado em
  TRUSTED_PROXY_COUNT — o mesmo IP confiável do gate de `tools`).
- `check_daily_quota`: cota diária por chave (ex.: convites por promotor), via cache.

O store é o Django cache: em prod, DatabaseCache (cross-worker, sem Redis — CONVENTION §8); em
dev/test, LocMemCache (por processo, suficiente). Config em core/settings (CACHES + THROTTLE_*).
"""

from __future__ import annotations

from django.core.cache import cache
from ninja.throttling import AnonRateThrottle

from core.net import client_ip


class ClientIpAnonThrottle(AnonRateThrottle):
    """AnonRateThrottle com IP CONFIÁVEL (core.net.client_ip) em vez do XFF-esquerdo forjável.

    A taxa vem de settings.THROTTLE_ANON_RATE (formato "N/period", ex.: "20/h"). O objeto é
    construído UMA vez (no decorator, em import), mas relê a taxa A CADA request — assim o .env
    de prod vale sem redeploy e o rate é configurável em runtime."""

    def __init__(self, rate: str | None = None) -> None:
        # rate=None: resolve dinamicamente em allow_request. rate explícito: fixo (uso raro/teste).
        self._fixed_rate = rate
        super().__init__(rate or "20/h")

    def get_ident(self, request):  # noqa: D102 — override: IP ancorado no proxy
        return client_ip(request) or super().get_ident(request)

    def allow_request(self, request):
        if self._fixed_rate is None:
            from django.conf import settings

            # relê a taxa atual (settings/.env) e reparseia num_requests/duration a cada request.
            self.num_requests, self.duration = self.parse_rate(
                settings.THROTTLE_ANON_RATE
            )
        return super().allow_request(request)


def check_daily_quota(*, scope: str, ident: str, limit: int) -> bool:
    """True se AINDA há cota hoje para (scope, ident); False se estourou. Incrementa ATOMICAMENTE.

    Cota diária por chave (ex.: convites por promotor) — o throttle do ninja é por-request/IP;
    esta é por-entidade/dia. Store = mesmo Django cache (DatabaseCache cross-worker em prod).

    `incr` é atômico (evita a race TOCTOU do get→compara→set: N requests concorrentes liam o mesmo
    `used` e todas passavam). Falha-ABERTO se o cache cair (anti-abuso, não gate de segurança):
    melhor deixar passar do que travar o promotor legítimo por cache indisponível."""
    if limit <= 0:
        return True
    from django.utils import timezone

    day = timezone.localdate().isoformat()
    key = f"daily_quota:{scope}:{ident}:{day}"
    try:
        # add() só cria se ausente (não zera um contador em curso); depois incr() atômico.
        cache.add(key, 0, 60 * 60 * 25)  # TTL 25h: cobre o dia + fuso, expira sozinho
        used = cache.incr(key)  # atômico: retorna o valor DEPOIS de somar 1
        return used <= limit
    except Exception:  # noqa: BLE001 — cache indisponível não pode travar o fluxo legítimo
        return True
