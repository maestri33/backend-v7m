"""Notificações de negócio: catálogo, renderização e despacho."""


def send_event(*args, **kwargs):
    from notifications.delivery import send_event as _send_event

    return _send_event(*args, **kwargs)


def send_adhoc(*args, **kwargs):
    from notifications.delivery import send_adhoc as _send_adhoc

    return _send_adhoc(*args, **kwargs)


__all__ = ["send_event", "send_adhoc"]
