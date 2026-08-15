from __future__ import annotations

import structlog

from integrations.notify import client

logger = structlog.get_logger()
_PERMANENT_STATUSES = frozenset({400, 404, 422})


def push_send(payload: dict) -> None:
    try:
        response = client.post_send(payload)
    except client.NotifyServerError as exc:
        if exc.status_code in _PERMANENT_STATUSES:
            logger.warning(
                "notify.push_dropped",
                external_id=payload.get("external_id"),
                caller=payload.get("caller"),
                status=exc.status_code,
            )
            return
        raise
    logger.info(
        "notify.pushed",
        external_id=payload.get("external_id"),
        server_id=response.get("external_id"),
        caller=payload.get("caller"),
    )
