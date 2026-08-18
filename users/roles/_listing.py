"""Teto de segurança das listagens sem paginação de verdade (auditoria API C1).

Não é paginação (que muda o contrato e precisa do front) — é uma trava anti-pathology: uma lista
sem `limit` nunca materializa a tabela inteira. Quando trunca, LOGA (a régua proíbe cap SILENCIOSO):
o warning sinaliza ao ops que a paginação de verdade virou necessária pra aquela superfície."""

from __future__ import annotations

import structlog

logger = structlog.get_logger()

# Teto default das listagens de coordenador/staff. Folgado (nenhum polo real chega perto), mas
# barra a materialização da tabela inteira em base grande (ex.: /staff/leads global).
LIST_HARD_CAP = 1000


def capped(queryset, *, event: str, cap: int = LIST_HARD_CAP, **log_ctx) -> list:
    """Materializa `queryset` até `cap` linhas. Se havia MAIS, corta e loga `event` (warning) com
    `log_ctx` — nunca trunca em silêncio. Devolve a lista (≤ cap)."""
    rows = list(queryset[: cap + 1])
    if len(rows) > cap:
        logger.warning(event, cap=cap, **log_ctx)
        return rows[:cap]
    return rows
