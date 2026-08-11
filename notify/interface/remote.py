"""Chamada ao notify-server: o wrapper que traduz falha (HTTP >=400 ou rede) no envelope padrão
`NOTIFY_SERVER_DOWN`. Dentro de `transaction.atomic`, a exceção desfaz a escrita local (espelho coeso).

Compartilhado pelo proxy do `/history` e pelas mutações de Template/Trigger (`notify.interface.staff`).
"""

from __future__ import annotations

from users.exceptions import IntegrationError


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
