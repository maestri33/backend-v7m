from __future__ import annotations

import httpx
import structlog
from django.conf import settings

logger = structlog.get_logger()


class NotifyServerError(Exception):
    def __init__(self, status_code: int, body, message: str = ""):
        self.status_code = status_code
        self.body = body
        super().__init__(message or f"notify-server {status_code}: {body!r}")


def _request(
    method: str,
    path: str,
    *,
    json: dict | None = None,
    params: dict | None = None,
    timeout: float | None = None,
) -> httpx.Response:
    with httpx.Client(
        base_url=settings.NOTIFY_SERVER_URL,
        headers={"Authorization": f"Bearer {settings.NOTIFY_API_KEY}"},
        timeout=timeout or settings.NOTIFY_TIMEOUT,
    ) as client:
        return client.request(method, path, json=json, params=params)


async def _request_async(
    method: str,
    path: str,
    *,
    json: dict | None = None,
    timeout: float | None = None,
) -> httpx.Response:
    async with httpx.AsyncClient(
        base_url=settings.NOTIFY_SERVER_URL,
        headers={"Authorization": f"Bearer {settings.NOTIFY_API_KEY}"},
        timeout=timeout or settings.NOTIFY_TIMEOUT,
    ) as client:
        return await client.request(method, path, json=json)


def _error_body(response):
    try:
        return response.json()
    except Exception:  # noqa: BLE001
        return response.text


def _ok(response, method: str, path: str):
    if response.status_code >= 400:
        logger.warning("notify.integration.error", method=method, path=path, status=response.status_code)
        raise NotifyServerError(response.status_code, _error_body(response))
    return response.json()


def post_send(payload: dict, *, run_sync: bool = False) -> dict:
    timeout = settings.NOTIFY_SYNC_TIMEOUT if run_sync else settings.NOTIFY_TIMEOUT
    response = _request("POST", "/v1/send", json=payload, timeout=timeout)
    return _ok(response, "POST", "/v1/send")


def phone_check(numbers: list[str]) -> list[dict]:
    response = _request("POST", "/v1/phone/check", json={"numbers": numbers})
    return _ok(response, "POST", "/v1/phone/check")


def phone_avatar(number: str) -> str | None:
    response = _request("POST", "/v1/phone/avatar", json={"number": number})
    photo = (_ok(response, "POST", "/v1/phone/avatar") or {}).get("photo")
    return photo if isinstance(photo, str) and photo else None


async def phone_check_async(numbers: list[str]) -> list[dict]:
    response = await _request_async("POST", "/v1/phone/check", json={"numbers": numbers})
    return _ok(response, "POST", "/v1/phone/check")
