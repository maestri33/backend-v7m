"""Modo remote (`NOTIFY_MODE=remote`): a flag + o wrapper de chamada ao notify-server.

Compartilhado pelo proxy do `/history` e pelas mutações de Template/Trigger (`notify.interface.staff`)
— antes viviam soltos no `api/staff_notify`. `server_call` traduz falha do servidor (HTTP >=400 ou
rede) no envelope padrão `NOTIFY_SERVER_DOWN`; dentro de `transaction.atomic`, a exceção desfaz a
escrita local (o espelho fica coeso)."""

from __future__ import annotations

from django.conf import settings

from users.exceptions import IntegrationError


def is_remote() -> bool:
    """Lê a flag a cada chamada (rollback = trocar NOTIFY_MODE + restart, sem redeploy)."""
    return settings.NOTIFY_MODE == "remote"


def server_call(fn):
    """Chama o notify-server; falha (HTTP >=400 ou rede) vira 502 `NOTIFY_SERVER_DOWN` no envelope
    padrão. Dentro do `transaction.atomic`, a exceção desfaz a escrita local."""
    import httpx

    from notify.sdk import client

    try:
        return fn()
    except (client.NotifyServerError, httpx.HTTPError) as exc:
        raise IntegrationError(
            "notify-server indisponível — tente novamente.",
            code="NOTIFY_SERVER_DOWN",
        ) from exc
